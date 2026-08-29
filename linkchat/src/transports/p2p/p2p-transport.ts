/**
 * Direct peer-to-peer transport: a WebSocket listener plus outbound dials.
 *
 * When this transport is used, chat traffic goes straight from one
 * participant's machine to another's. No server sees it, no server relays it,
 * and there is no central instance to run. The only thing a third party ever
 * provided is the link itself, and that was sent by the user.
 *
 * WHAT THIS DOES NOT DO — and the docs say the same thing:
 *
 *   There is no NAT traversal here. No STUN, no TURN, no ICE, no hole
 *   punching. A dial succeeds when the advertised ws:// address is actually
 *   routable from the dialling machine: same LAN, same host, a VPN/overlay
 *   like Tailscale, or a forwarded port. Two peers behind ordinary home NATs
 *   with no rendezvous will NOT connect directly, and this transport reports
 *   that honestly (`canReach` goes false, the router falls back to SMTP, and
 *   the UI badge changes from "Direct" to "SMTP"). Adding real traversal
 *   means adding ICE, which means a signalling service and a TURN relay for
 *   the ~10-20% of pairs that cannot punch through; see
 *   docs/LINKCHAT_PROTOCOL.md § "NAT, and what is not solved".
 *
 * Transport security: frames on the wire are plain WebSocket unless a `wss://`
 * URL and TLS options are supplied. That is deliberate and stated rather than
 * papered over: record bodies are already end-to-end encrypted, so a passive
 * observer on the LAN learns metadata (who talks to whom, when, how much) but
 * not content. Connections are still authenticated, by the challenge-response
 * handshake the node layer performs over them.
 */
import { networkInterfaces } from "node:os";
import { WebSocket, WebSocketServer } from "ws";
import type { SignedFrame, TransportName } from "../../protocol/types.ts";
import {
  TransportUnavailableError,
  hintsFor,
  type ConnectionInfo,
  type PeerAddress,
  type Transport,
  type TransportHandlers,
  type TransportStatus,
} from "../transport.ts";

export type P2POptions = {
  host?: string;
  /** 0 asks the OS for a free port; the real one is on `address` after start. */
  port?: number;
  /** Host to advertise in invite links. Defaults to a LAN address if found. */
  advertiseHost?: string;
  maxPayloadBytes?: number;
  dialTimeoutMs?: number;
};

type Connection = {
  info: ConnectionInfo;
  socket: WebSocket;
  latencyMs?: number;
};

let connectionCounter = 0;

export class P2PTransport implements Transport {
  readonly name: TransportName = "p2p";
  readonly delivery = "immediate" as const;
  readonly #options: Required<P2POptions>;
  readonly #connections = new Map<string, Connection>();
  readonly #byPeer = new Map<string, string>();
  readonly #dialing = new Map<string, Promise<Connection>>();
  #server: WebSocketServer | null = null;
  #handlers: TransportHandlers | null = null;
  #port = 0;
  #sent = 0;
  #received = 0;
  #failures = 0;

  constructor(options: P2POptions = {}) {
    this.#options = {
      host: options.host ?? "0.0.0.0",
      port: options.port ?? 0,
      advertiseHost: options.advertiseHost ?? defaultAdvertiseHost(),
      maxPayloadBytes: options.maxPayloadBytes ?? 1024 * 1024,
      dialTimeoutMs: options.dialTimeoutMs ?? 4000,
    };
  }

  attach(handlers: TransportHandlers): void {
    this.#handlers = handlers;
  }

  get port(): number {
    return this.#port;
  }

  /** The address to publish in invite links and profile records. */
  get advertisedUrl(): string {
    return `ws://${this.#options.advertiseHost}:${this.#port}`;
  }

  async start(): Promise<void> {
    if (this.#server) return;
    const server = new WebSocketServer({
      host: this.#options.host,
      port: this.#options.port,
      maxPayload: this.#options.maxPayloadBytes,
    });
    this.#server = server;
    await new Promise<void>((resolve, reject) => {
      server.once("listening", () => {
        const address = server.address();
        this.#port = typeof address === "object" && address ? address.port : this.#options.port;
        resolve();
      });
      server.once("error", reject);
    });
    server.on("connection", (socket, request) => {
      this.#adopt(socket, "inbound", request.socket.remoteAddress ?? "unknown");
    });
  }

  async stop(): Promise<void> {
    for (const connection of [...this.#connections.values()]) {
      connection.socket.close(1001, "node shutting down");
    }
    this.#connections.clear();
    this.#byPeer.clear();
    const server = this.#server;
    this.#server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  #adopt(socket: WebSocket, direction: "inbound" | "outbound", remote: string): Connection {
    connectionCounter += 1;
    const info: ConnectionInfo = {
      connectionId: `p2p-${direction}-${connectionCounter}`,
      transport: "p2p",
      direction,
      remote,
    };
    const connection: Connection = { info, socket };
    this.#connections.set(info.connectionId, connection);

    socket.on("message", (data) => {
      this.#received += 1;
      let frame: SignedFrame;
      try {
        frame = JSON.parse(data.toString()) as SignedFrame;
      } catch {
        this.#failures += 1;
        return;
      }
      const latency =
        typeof frame.sent_at === "number" ? Math.max(0, Date.now() - frame.sent_at) : undefined;
      this.#handlers?.onFrame({
        frame,
        transport: "p2p",
        connectionId: info.connectionId,
        remote,
        ...(latency === undefined ? {} : { latencyMs: latency }),
      });
    });
    socket.on("close", () => {
      this.#connections.delete(info.connectionId);
      if (info.peerId && this.#byPeer.get(info.peerId) === info.connectionId) {
        this.#byPeer.delete(info.peerId);
      }
      this.#handlers?.onConnectionClose?.(info);
    });
    socket.on("error", () => {
      this.#failures += 1;
    });
    socket.on("pong", (payload) => {
      const sentAt = Number(payload.toString());
      if (Number.isFinite(sentAt)) connection.latencyMs = Date.now() - sentAt;
    });

    this.#handlers?.onConnectionOpen?.(info);
    return connection;
  }

  bindPeer(connectionId: string, peerId: string): void {
    const connection = this.#connections.get(connectionId);
    if (!connection) return;
    connection.info.peerId = peerId;
    this.#byPeer.set(peerId, connectionId);
  }

  #liveConnection(peerId: string): Connection | null {
    const connectionId = this.#byPeer.get(peerId);
    if (!connectionId) return null;
    const connection = this.#connections.get(connectionId);
    if (!connection || connection.socket.readyState !== WebSocket.OPEN) return null;
    return connection;
  }

  isConnected(peerId: string): boolean {
    return this.#liveConnection(peerId) !== null;
  }

  latencyTo(peerId: string): number | undefined {
    return this.#liveConnection(peerId)?.latencyMs;
  }

  /**
   * True if we already hold a live connection, or the peer published a p2p
   * address worth dialling. A published address is not proof of
   * reachability — the dial in `send` is what proves it, and it is allowed
   * to fail.
   */
  canReach(peer: PeerAddress): boolean {
    if (!this.#server) return false;
    if (this.#liveConnection(peer.peerId)) return true;
    return hintsFor(peer, "p2p").length > 0;
  }

  /** A socket that is open right now - not merely an address we could dial. */
  connectedTo(peer: PeerAddress): boolean {
    return this.#liveConnection(peer.peerId) !== null;
  }

  async send(peer: PeerAddress, frame: SignedFrame): Promise<void> {
    const connection = this.#liveConnection(peer.peerId) ?? (await this.#dial(peer));
    await new Promise<void>((resolve, reject) => {
      connection.socket.send(JSON.stringify(frame), (error) => {
        if (error) {
          this.#failures += 1;
          reject(new TransportUnavailableError("p2p", `send to ${peer.peerId} failed: ${error.message}`));
          return;
        }
        this.#sent += 1;
        resolve();
      });
    });
  }

  /** Dial every advertised address in turn; first one to open wins. */
  async #dial(peer: PeerAddress): Promise<Connection> {
    const existing = this.#dialing.get(peer.peerId);
    if (existing) return existing;

    const urls = hintsFor(peer, "p2p").map((hint) => hint.url);
    if (urls.length === 0) {
      throw new TransportUnavailableError("p2p", `${peer.peerId} advertises no p2p address`);
    }

    const attempt = (async () => {
      const failures: string[] = [];
      for (const url of urls) {
        try {
          const connection = await this.#open(url);
          this.bindPeer(connection.info.connectionId, peer.peerId);
          return connection;
        } catch (error) {
          failures.push(`${url}: ${(error as Error).message}`);
        }
      }
      this.#failures += 1;
      throw new TransportUnavailableError(
        "p2p",
        `no direct route to ${peer.peerId} (${failures.join("; ")})`,
      );
    })();

    this.#dialing.set(peer.peerId, attempt);
    try {
      return await attempt;
    } finally {
      this.#dialing.delete(peer.peerId);
    }
  }

  async #open(url: string): Promise<Connection> {
    const socket = new WebSocket(url, { maxPayload: this.#options.maxPayloadBytes });
    return await new Promise<Connection>((resolve, reject) => {
      const timer = setTimeout(() => {
        socket.terminate();
        reject(new Error(`dial timed out after ${this.#options.dialTimeoutMs}ms`));
      }, this.#options.dialTimeoutMs);
      socket.once("open", () => {
        clearTimeout(timer);
        resolve(this.#adopt(socket, "outbound", url));
      });
      socket.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  /** Explicitly dial a peer (used by the node on startup and on reconnect). */
  async connectTo(peer: PeerAddress): Promise<ConnectionInfo> {
    const connection = this.#liveConnection(peer.peerId) ?? (await this.#dial(peer));
    return connection.info;
  }

  /** Dial an address before we know whose it is, as a joiner must. */
  async connectToUrl(url: string): Promise<ConnectionInfo> {
    const connection = await this.#open(url);
    return connection.info;
  }

  sendOnConnection(connectionId: string, frame: SignedFrame): void {
    const connection = this.#connections.get(connectionId);
    if (!connection || connection.socket.readyState !== WebSocket.OPEN) {
      throw new TransportUnavailableError("p2p", `connection ${connectionId} is not open`);
    }
    connection.socket.send(JSON.stringify(frame));
    this.#sent += 1;
  }

  /** Round-trip probe; the reply updates `latencyTo`. */
  ping(): void {
    for (const connection of this.#connections.values()) {
      if (connection.socket.readyState === WebSocket.OPEN) {
        connection.socket.ping(Buffer.from(String(Date.now())));
      }
    }
  }

  connections(): ConnectionInfo[] {
    return [...this.#connections.values()].map((connection) => ({
      ...connection.info,
      ...(connection.latencyMs === undefined ? {} : { latencyMs: connection.latencyMs }),
    }));
  }

  status(): TransportStatus {
    return {
      name: "p2p",
      running: this.#server !== null,
      detail: this.#server ? `listening on ${this.advertisedUrl}` : "stopped",
      framesSent: this.#sent,
      framesReceived: this.#received,
      failures: this.#failures,
      connections: this.#connections.size,
    };
  }
}

function defaultAdvertiseHost(): string {
  for (const addresses of Object.values(networkInterfaces())) {
    for (const address of addresses ?? []) {
      if (address.family === "IPv4" && !address.internal) return address.address;
    }
  }
  return "127.0.0.1";
}
