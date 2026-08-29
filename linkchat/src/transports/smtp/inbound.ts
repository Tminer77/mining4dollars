/**
 * Inbound mail sources.
 *
 * Receiving is where "SMTP as a transport" gets honest. SMTP is a *sending*
 * protocol: a node cannot pull mail over SMTP, so something has to put the
 * message where the node can see it. Two adapters cover the realistic cases:
 *
 *   SmtpListenerSource — the node speaks SMTP itself and accepts deliveries
 *                        directly. This is what a self-hosted MX or the local
 *                        dev MTA relays into, and it is a real SMTP server.
 *
 *   MaildirSource      — the node watches a directory of RFC 5322 files. This
 *                        is the shape you get from procmail, fetchmail,
 *                        getmail, or `mbsync` against a normal provider
 *                        mailbox, and it is how you would run this against
 *                        Gmail or Fastmail today without writing an IMAP
 *                        client.
 *
 * An IMAP IDLE source is the obvious third adapter and is deliberately not
 * included: it would be the only component here that could not be exercised
 * by the test suite without live credentials.
 */
import { readFileSync, readdirSync, renameSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { SMTPServer } from "smtp-server";
import type { SignedFrame } from "../../protocol/types.ts";
import { rawMailToFrame } from "./mime.ts";

export type InboundMailHandler = (frame: SignedFrame, meta: { via: string }) => void;

export interface InboundMailSource {
  readonly description: string;
  start(handler: InboundMailHandler): Promise<void>;
  stop(): Promise<void>;
}

export type SmtpListenerOptions = {
  port?: number;
  host?: string;
  /** Addresses this node answers for. Empty accepts anything (dev default). */
  addresses?: string[];
  maxSizeBytes?: number;
};

/** A real SMTP server, listening for deliveries addressed to this node. */
export class SmtpListenerSource implements InboundMailSource {
  readonly #options: Required<SmtpListenerOptions>;
  #server: SMTPServer | null = null;
  #port = 0;

  constructor(options: SmtpListenerOptions = {}) {
    this.#options = {
      port: options.port ?? 0,
      host: options.host ?? "127.0.0.1",
      addresses: options.addresses ?? [],
      maxSizeBytes: options.maxSizeBytes ?? 5 * 1024 * 1024,
    };
  }

  get port(): number {
    return this.#port;
  }

  get description(): string {
    return `smtp listener on ${this.#options.host}:${this.#port}`;
  }

  async start(handler: InboundMailHandler): Promise<void> {
    const allowed = new Set(this.#options.addresses.map((address) => address.toLowerCase()));
    const server = new SMTPServer({
      authOptional: true,
      disabledCommands: ["AUTH", "STARTTLS"],
      size: this.#options.maxSizeBytes,
      onRcptTo(address, _session, callback) {
        if (allowed.size > 0 && !allowed.has(address.address.toLowerCase())) {
          callback(new Error(`550 no mailbox here for ${address.address}`));
          return;
        }
        callback();
      },
      onData(stream, session, callback) {
        const chunks: Buffer[] = [];
        stream.on("data", (chunk: Buffer) => chunks.push(chunk));
        stream.on("end", () => {
          void (async () => {
            try {
              const frame = await rawMailToFrame(Buffer.concat(chunks));
              if (frame) handler(frame, { via: session.remoteAddress ?? "smtp" });
            } catch {
              // A malformed message is not worth refusing the SMTP
              // transaction over; the sender cannot fix it by retrying.
            }
            callback();
          })();
        });
      },
    });
    this.#server = server;
    await new Promise<void>((resolve, reject) => {
      server.listen(this.#options.port, this.#options.host, () => {
        const address = server.server.address();
        this.#port = typeof address === "object" && address ? address.port : this.#options.port;
        resolve();
      });
      server.once("error", reject);
    });
  }

  async stop(): Promise<void> {
    const server = this.#server;
    this.#server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

export type MaildirOptions = {
  /** Directory that a delivery agent drops RFC 5322 files into. */
  path: string;
  pollMs?: number;
};

/** Polls a directory of .eml files; processed files move to `cur/`. */
export class MaildirSource implements InboundMailSource {
  readonly #path: string;
  readonly #pollMs: number;
  #timer: NodeJS.Timeout | null = null;

  constructor(options: MaildirOptions) {
    this.#path = options.path;
    this.#pollMs = options.pollMs ?? 1000;
  }

  get description(): string {
    return `maildir at ${this.#path}`;
  }

  async start(handler: InboundMailHandler): Promise<void> {
    mkdirSync(join(this.#path, "new"), { recursive: true });
    mkdirSync(join(this.#path, "cur"), { recursive: true });
    const scan = async (): Promise<void> => {
      const newDir = join(this.#path, "new");
      if (!existsSync(newDir)) return;
      for (const name of readdirSync(newDir)) {
        const source = join(newDir, name);
        try {
          const frame = await rawMailToFrame(readFileSync(source));
          if (frame) handler(frame, { via: `maildir:${name}` });
        } catch {
          // Leave unreadable files where they are rather than losing them.
          continue;
        }
        renameSync(source, join(this.#path, "cur", name));
      }
    };
    await scan();
    this.#timer = setInterval(() => void scan(), this.#pollMs);
    this.#timer.unref();
  }

  async stop(): Promise<void> {
    if (this.#timer) clearInterval(this.#timer);
    this.#timer = null;
  }
}
