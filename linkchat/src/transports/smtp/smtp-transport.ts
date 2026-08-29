/**
 * SMTP transport: store-and-forward delivery for peers that are not reachable
 * directly.
 *
 * The value of SMTP here is not that it is email. It is that the internet
 * already runs a global, federated, authenticated, retrying store-and-forward
 * network that every ISP permits and that will hold a message for a machine
 * that is switched off. LinkChat borrows that plumbing and puts its own
 * protocol inside it.
 *
 * Known constraints, none of which this code pretends away:
 *
 *   - Latency is minutes, not milliseconds. Greylisting alone can cost 15.
 *   - Many networks block outbound port 25, so relaying through a submission
 *     server (587/465 with credentials) is the norm, not the exception.
 *   - Provider rate limits and spam heuristics apply. A conversation that
 *     sends thousands of frames over SMTP will get an account suspended, and
 *     that is a real limit on this transport, not a bug to work around.
 *   - Receiving requires something that puts mail where the node can read it
 *     (see inbound.ts). SMTP itself cannot pull.
 *   - The recipient address is metadata visible to every hop.
 *
 * Credentials come from the environment. None are ever written to disk by
 * this module, and none appear in a link.
 */
import { createTransport, type Transporter } from "nodemailer";
import type { SignedFrame, TransportName } from "../../protocol/types.ts";
import {
  TransportUnavailableError,
  hintsFor,
  type PeerAddress,
  type Transport,
  type TransportHandlers,
  type TransportStatus,
} from "../transport.ts";
import type { InboundMailSource } from "./inbound.ts";
import { frameToMail } from "./mime.ts";

export type SmtpRelayConfig = {
  host: string;
  port: number;
  /** true for implicit TLS (465); false uses STARTTLS when offered. */
  secure: boolean;
  auth?: { user: string; pass: string };
  /** Envelope sender; also the address peers reply to. */
  from: string;
  /** Dev only: allow a plaintext relay with no TLS at all. */
  allowInsecure?: boolean;
};

export type SmtpTransportOptions = {
  relay: SmtpRelayConfig;
  inbound?: InboundMailSource;
  /** Our own address, advertised to peers so they can reach us. */
  address: string;
};

export class SmtpTransport implements Transport {
  readonly name: TransportName = "smtp";
  /** Accepted for later delivery: the peer may be offline for hours. */
  readonly delivery = "deferred" as const;
  readonly address: string;
  readonly #relay: SmtpRelayConfig;
  readonly #inbound: InboundMailSource | undefined;
  #transporter: Transporter | null = null;
  #handlers: TransportHandlers | null = null;
  #running = false;
  #sent = 0;
  #received = 0;
  #failures = 0;
  #lastError: string | null = null;

  constructor(options: SmtpTransportOptions) {
    this.#relay = options.relay;
    this.#inbound = options.inbound;
    this.address = options.address;
  }

  attach(handlers: TransportHandlers): void {
    this.#handlers = handlers;
  }

  async start(): Promise<void> {
    this.#transporter = createTransport({
      host: this.#relay.host,
      port: this.#relay.port,
      secure: this.#relay.secure,
      ...(this.#relay.auth ? { auth: this.#relay.auth } : {}),
      // Refuse to silently downgrade: if the operator did not opt into an
      // insecure relay, require TLS on anything that is not implicit-TLS.
      ...(this.#relay.allowInsecure ? { ignoreTLS: true } : { requireTLS: !this.#relay.secure }),
      tls: { rejectUnauthorized: !this.#relay.allowInsecure },
    });
    await this.#inbound?.start((frame, meta) => {
      this.#received += 1;
      this.#handlers?.onFrame({ frame, transport: "smtp", remote: meta.via });
    });
    this.#running = true;
  }

  async stop(): Promise<void> {
    this.#running = false;
    await this.#inbound?.stop();
    this.#transporter?.close();
    this.#transporter = null;
  }

  /** Reachable if the peer published an address for us to send to. */
  canReach(peer: PeerAddress): boolean {
    return this.#running && hintsFor(peer, "smtp").length > 0;
  }

  async send(peer: PeerAddress, frame: SignedFrame): Promise<void> {
    const to = hintsFor(peer, "smtp")[0]?.address;
    if (!to) throw new TransportUnavailableError("smtp", `${peer.peerId} advertises no address`);
    if (!this.#transporter) throw new TransportUnavailableError("smtp", "transport is not started");

    const mail = frameToMail(frame);
    try {
      await this.#transporter.sendMail({
        from: this.#relay.from,
        to,
        subject: mail.subject,
        text: mail.text,
        headers: mail.headers,
        attachments: [
          {
            filename: mail.attachmentFilename,
            contentType: mail.contentType,
            content: mail.content,
          },
        ],
      });
      this.#sent += 1;
      this.#lastError = null;
    } catch (error) {
      this.#failures += 1;
      this.#lastError = (error as Error).message;
      // A relay refusal is a real failure to hand off; the router decides
      // whether to retry. Accepting it here would be claiming a delivery
      // that did not happen.
      throw new TransportUnavailableError("smtp", `relay refused: ${this.#lastError}`);
    }
  }

  /** Prove the relay actually answers, rather than assuming it will. */
  async verifyRelay(): Promise<{ ok: boolean; detail: string }> {
    if (!this.#transporter) return { ok: false, detail: "not started" };
    try {
      await this.#transporter.verify();
      return { ok: true, detail: `relay ${this.#relay.host}:${this.#relay.port} ready` };
    } catch (error) {
      this.#lastError = (error as Error).message;
      return { ok: false, detail: this.#lastError };
    }
  }

  status(): TransportStatus {
    const inbound = this.#inbound ? this.#inbound.description : "send-only (no inbound source)";
    return {
      name: "smtp",
      running: this.#running,
      detail: this.#running
        ? `relay ${this.#relay.host}:${this.#relay.port} as ${this.address}; inbound: ${inbound}` +
          (this.#lastError ? `; last error: ${this.#lastError}` : "")
        : "stopped",
      framesSent: this.#sent,
      framesReceived: this.#received,
      failures: this.#failures,
    };
  }
}

/**
 * Build a relay config from the environment. Nothing here has a default that
 * would silently point at a real mail provider.
 */
export function relayFromEnv(env: NodeJS.ProcessEnv = process.env): SmtpRelayConfig | null {
  const host = env.LINKCHAT_SMTP_HOST;
  const from = env.LINKCHAT_SMTP_FROM;
  if (!host || !from) return null;
  const user = env.LINKCHAT_SMTP_USER;
  const pass = env.LINKCHAT_SMTP_PASS;
  return {
    host,
    port: Number(env.LINKCHAT_SMTP_PORT ?? 587),
    secure: env.LINKCHAT_SMTP_SECURE === "true",
    ...(user && pass ? { auth: { user, pass } } : {}),
    from,
    allowInsecure: env.LINKCHAT_SMTP_ALLOW_INSECURE === "true",
  };
}
