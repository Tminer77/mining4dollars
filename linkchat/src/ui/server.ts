/**
 * The local UI server.
 *
 * The browser is a client of *your own* node, over loopback. It is not a
 * participant in the protocol: it holds no keys, signs nothing, and cannot
 * talk to anyone else's node. That separation is what keeps the private key
 * out of reach of page script, and it is why LinkChat is a small local
 * process with a web UI rather than a web app.
 *
 * Anyone who can reach this port can act as you, so it binds to 127.0.0.1 and
 * requires a token minted at startup. On a shared machine that token is the
 * only thing between another local user and your conversations.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, type WebSocket } from "ws";
import { toBase64Url } from "../crypto/encoding.ts";
import { randomBytes, constantTimeEquals } from "../crypto/primitives.ts";
import type { LinkChatNode } from "../node/node.ts";

const here = dirname(fileURLToPath(import.meta.url));

export type UiServerOptions = {
  node: LinkChatNode;
  port?: number;
  host?: string;
  /** Omit to mint one. Supplying a fixed token makes dev URLs stable. */
  token?: string;
};

type ClientCommand =
  | { type: "create"; title?: string }
  | { type: "join"; link: string }
  | { type: "send"; conversationId: string; text: string }
  | { type: "invite"; conversationId: string; ttlMinutes?: number }
  | { type: "revoke"; conversationId: string; nonce: string }
  | { type: "refresh" };

export class UiServer {
  readonly #node: LinkChatNode;
  readonly #token: string;
  readonly #host: string;
  readonly #requestedPort: number;
  readonly #sockets = new Set<WebSocket>();
  #server: ReturnType<typeof createServer> | null = null;
  #wss: WebSocketServer | null = null;
  #port = 0;

  constructor(options: UiServerOptions) {
    this.#node = options.node;
    this.#token = options.token ?? toBase64Url(randomBytes(12));
    this.#host = options.host ?? "127.0.0.1";
    this.#requestedPort = options.port ?? 0;
  }

  get url(): string {
    return `http://${this.#host}:${this.#port}/?t=${this.#token}`;
  }

  get port(): number {
    return this.#port;
  }

  async start(): Promise<void> {
    const server = createServer((request, response) => this.#onRequest(request, response));
    this.#server = server;
    await new Promise<void>((resolve, reject) => {
      server.listen(this.#requestedPort, this.#host, () => {
        const address = server.address();
        this.#port = typeof address === "object" && address ? address.port : this.#requestedPort;
        resolve();
      });
      server.once("error", reject);
    });

    const wss = new WebSocketServer({ noServer: true });
    this.#wss = wss;
    server.on("upgrade", (request, socket, head) => {
      if (!this.#authorised(request)) {
        socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
        socket.destroy();
        return;
      }
      wss.handleUpgrade(request, socket, head, (ws) => this.#onSocket(ws));
    });

    this.#node.on("update", () => this.broadcast());
    this.#node.on("message", () => this.broadcast());
    this.#node.diagnostics.onEvent(() => this.broadcast());
  }

  async stop(): Promise<void> {
    for (const socket of this.#sockets) socket.close();
    this.#sockets.clear();
    this.#wss?.close();
    const server = this.#server;
    this.#server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  #authorised(request: IncomingMessage): boolean {
    const url = new URL(request.url ?? "/", `http://${this.#host}`);
    const supplied = url.searchParams.get("t") ?? "";
    const expected = this.#token;
    // Compare in constant time, and only when lengths match, so the token
    // cannot be recovered a character at a time.
    if (supplied.length !== expected.length) return false;
    return constantTimeEquals(Buffer.from(supplied), Buffer.from(expected));
  }

  #onRequest(request: IncomingMessage, response: ServerResponse): void {
    const url = new URL(request.url ?? "/", `http://${this.#host}`);

    // A LinkChat link opened in this browser lands on /join/<id>#... The
    // fragment never reaches the server, so the page reads it in script and
    // hands it back over the local socket.
    if (url.pathname === "/" || url.pathname.startsWith("/join/")) {
      if (!this.#authorised(request) && url.pathname === "/") {
        response.writeHead(401, { "content-type": "text/plain" });
        response.end("LinkChat: missing or bad token. Use the URL printed at startup.\n");
        return;
      }
      const html = readFileSync(join(here, "public", "index.html"), "utf8");
      response.writeHead(200, {
        "content-type": "text/html; charset=utf-8",
        // The UI is entirely self-contained; nothing loads from the network.
        "content-security-policy":
          "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*",
        "referrer-policy": "no-referrer",
      });
      response.end(html);
      return;
    }

    if (url.pathname === "/api/status") {
      if (!this.#authorised(request)) {
        response.writeHead(401).end();
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(this.#node.status(), null, 2));
      return;
    }

    response.writeHead(404, { "content-type": "text/plain" });
    response.end("not found\n");
  }

  #onSocket(socket: WebSocket): void {
    this.#sockets.add(socket);
    socket.on("close", () => this.#sockets.delete(socket));
    socket.on("message", (raw) => {
      void this.#onCommand(socket, raw.toString());
    });
    this.#push(socket);
  }

  async #onCommand(socket: WebSocket, raw: string): Promise<void> {
    let command: ClientCommand;
    try {
      command = JSON.parse(raw) as ClientCommand;
    } catch {
      return;
    }
    try {
      switch (command.type) {
        case "create":
          this.#node.createConversation(command.title?.trim() || "Conversation");
          break;
        case "join":
          await this.#node.joinByLink(command.link);
          break;
        case "send":
          await this.#node.post(command.conversationId, command.text);
          break;
        case "invite": {
          const invite = this.#node.createInvite(
            command.conversationId,
            command.ttlMinutes ? command.ttlMinutes * 60_000 : undefined,
          );
          socket.send(JSON.stringify({ type: "invite", ...invite }));
          break;
        }
        case "revoke":
          await this.#node.revokeInvite(command.conversationId, command.nonce);
          break;
        case "refresh":
          break;
      }
    } catch (error) {
      socket.send(JSON.stringify({ type: "error", message: (error as Error).message }));
    }
    this.broadcast();
  }

  #snapshot(): string {
    return JSON.stringify({
      type: "state",
      self: {
        peerId: this.#node.identity.peerId,
        displayName: this.#node.identity.displayName,
      },
      views: this.#node.views(),
      status: this.#node.status(),
      diagnostics: this.#node.diagnostics.events(60),
    });
  }

  #push(socket: WebSocket): void {
    if (socket.readyState === socket.OPEN) socket.send(this.#snapshot());
  }

  broadcast(): void {
    if (this.#sockets.size === 0) return;
    const payload = this.#snapshot();
    for (const socket of this.#sockets) {
      if (socket.readyState === socket.OPEN) socket.send(payload);
    }
  }
}
