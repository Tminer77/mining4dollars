/**
 * Transport selection and fallback.
 *
 * The rule the user experiences is "press send and it arrives". The rule the
 * router implements is: try the fastest transport that claims it can reach
 * this peer; if it fails, try the next; if none can, hold the frame and retry
 * with backoff.
 *
 *     direct P2P available?  --yes-->  P2P
 *              |
 *              no
 *              v
 *     peer published an address?  --yes-->  SMTP (store-and-forward)
 *              |
 *              no
 *              v
 *     outbox, retried with backoff; the log will also heal this
 *     on the next anti-entropy sync
 *
 * The outbox is deliberately not the durability story. Durability comes from
 * the replicated log: every record is on disk before it is sent, and a peer
 * that missed it asks for it on reconnect. The outbox only smooths over a
 * transport that is briefly unavailable, so losing it on restart costs
 * nothing but a little latency.
 */
import type { SignedFrame, TransportName } from "../protocol/types.ts";
import type { PeerAddress, Transport, TransportHandlers } from "./transport.ts";

export type RouteState = "direct" | "smtp" | "local" | "offline";

export type PeerRoute = {
  peerId: string;
  state: RouteState;
  lastTransport: TransportName | null;
  lastError: string | null;
  lastDeliveryAt: number | null;
  queued: number;
};

export type DeliveryResult = {
  ok: boolean;
  transport: TransportName | null;
  /** True when the frame was queued rather than handed to a transport. */
  queued: boolean;
  error?: string;
};

type OutboxEntry = {
  peer: PeerAddress;
  frame: SignedFrame;
  attempts: number;
  nextAttemptAt: number;
  lastError: string;
};

export type RouterOptions = {
  transports: Transport[];
  /** Preference order. First transport that `canReach` the peer wins. */
  preference?: TransportName[];
  maxAttempts?: number;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  now?: () => number;
};

export class TransportRouter {
  readonly #transports: Transport[];
  readonly #preference: TransportName[];
  readonly #routes = new Map<string, PeerRoute>();
  readonly #outbox: OutboxEntry[] = [];
  readonly #maxAttempts: number;
  readonly #baseBackoffMs: number;
  readonly #maxBackoffMs: number;
  readonly #now: () => number;
  #dropped = 0;

  constructor(options: RouterOptions) {
    this.#transports = options.transports;
    this.#preference = options.preference ?? ["p2p", "local", "smtp"];
    this.#maxAttempts = options.maxAttempts ?? 8;
    this.#baseBackoffMs = options.baseBackoffMs ?? 2000;
    this.#maxBackoffMs = options.maxBackoffMs ?? 5 * 60 * 1000;
    this.#now = options.now ?? Date.now;
  }

  get transports(): Transport[] {
    return [...this.#transports];
  }

  get(name: TransportName): Transport | undefined {
    return this.#transports.find((transport) => transport.name === name);
  }

  attach(handlers: TransportHandlers): void {
    for (const transport of this.#transports) transport.attach(handlers);
  }

  async start(): Promise<void> {
    for (const transport of this.#transports) await transport.start();
  }

  async stop(): Promise<void> {
    for (const transport of this.#transports) await transport.stop();
  }

  #ordered(): Transport[] {
    return [...this.#transports].sort(
      (a, b) => indexOrLast(this.#preference, a.name) - indexOrLast(this.#preference, b.name),
    );
  }

  async send(peer: PeerAddress, frame: SignedFrame): Promise<DeliveryResult> {
    const errors: string[] = [];
    for (const transport of this.#ordered()) {
      if (!transport.canReach(peer)) continue;
      try {
        await transport.send(peer, frame);
        this.#note(peer.peerId, {
          state: routeState(transport.name),
          lastTransport: transport.name,
          lastError: null,
          lastDeliveryAt: this.#now(),
        });
        return { ok: true, transport: transport.name, queued: false };
      } catch (error) {
        errors.push(`${transport.name}: ${(error as Error).message}`);
      }
    }

    const detail = errors.length > 0 ? errors.join("; ") : "no transport can reach this peer";
    this.#outbox.push({
      peer,
      frame,
      attempts: 1,
      nextAttemptAt: this.#now() + this.#baseBackoffMs,
      lastError: detail,
    });
    this.#note(peer.peerId, { state: "offline", lastError: detail });
    return { ok: false, transport: null, queued: true, error: detail };
  }

  /** Send to many peers; failures for one peer never block another. */
  async broadcast(peers: PeerAddress[], frame: SignedFrame): Promise<Map<string, DeliveryResult>> {
    const results = await Promise.all(
      peers.map(async (peer) => [peer.peerId, await this.send(peer, frame)] as const),
    );
    return new Map(results);
  }

  /** Retry everything whose backoff has elapsed. Called by the node's timer. */
  async flushOutbox(): Promise<{ delivered: number; stillQueued: number; dropped: number }> {
    const now = this.#now();
    const pending = this.#outbox.splice(0, this.#outbox.length);
    let delivered = 0;
    let dropped = 0;

    for (const entry of pending) {
      if (entry.nextAttemptAt > now) {
        this.#outbox.push(entry);
        continue;
      }
      let sent = false;
      for (const transport of this.#ordered()) {
        if (!transport.canReach(entry.peer)) continue;
        try {
          await transport.send(entry.peer, entry.frame);
          this.#note(entry.peer.peerId, {
            state: routeState(transport.name),
            lastTransport: transport.name,
            lastError: null,
            lastDeliveryAt: now,
          });
          delivered += 1;
          sent = true;
          break;
        } catch (error) {
          entry.lastError = `${transport.name}: ${(error as Error).message}`;
        }
      }
      if (sent) continue;
      entry.attempts += 1;
      if (entry.attempts > this.#maxAttempts) {
        // Give up on this *frame*, not on the message: the record is on disk
        // and anti-entropy sync will carry it when the peer reappears.
        dropped += 1;
        this.#dropped += 1;
        continue;
      }
      entry.nextAttemptAt = now + this.#backoff(entry.attempts);
      this.#outbox.push(entry);
    }

    for (const [peerId, route] of this.#routes) {
      route.queued = this.#outbox.filter((entry) => entry.peer.peerId === peerId).length;
    }
    return { delivered, stillQueued: this.#outbox.length, dropped };
  }

  #backoff(attempts: number): number {
    return Math.min(this.#maxBackoffMs, this.#baseBackoffMs * 2 ** (attempts - 1));
  }

  #note(peerId: string, patch: Partial<PeerRoute>): void {
    const existing = this.#routes.get(peerId) ?? {
      peerId,
      state: "offline" as RouteState,
      lastTransport: null,
      lastError: null,
      lastDeliveryAt: null,
      queued: 0,
    };
    this.#routes.set(peerId, { ...existing, ...patch });
  }

  /** Current state for a peer, refreshed against live transport reachability. */
  route(peer: PeerAddress): PeerRoute {
    const existing = this.#routes.get(peer.peerId) ?? {
      peerId: peer.peerId,
      state: "offline" as RouteState,
      lastTransport: null,
      lastError: null,
      lastDeliveryAt: null,
      queued: 0,
    };
    // Report what is actually live. A transport that could be dialled but
    // has no connection is not a connection, and showing "Direct" for a peer
    // whose machine is off would be exactly the kind of lie this project is
    // not allowed to tell.
    let state: RouteState = "offline";
    for (const transport of this.#ordered()) {
      const live = transport.connectedTo ? transport.connectedTo(peer) : transport.canReach(peer);
      if (live) {
        state = routeState(transport.name);
        break;
      }
    }
    const route: PeerRoute = {
      ...existing,
      state,
      queued: this.#outbox.filter((entry) => entry.peer.peerId === peer.peerId).length,
    };
    this.#routes.set(peer.peerId, route);
    return route;
  }

  get outboxSize(): number {
    return this.#outbox.length;
  }

  get droppedCount(): number {
    return this.#dropped;
  }
}

function routeState(name: TransportName): RouteState {
  if (name === "p2p") return "direct";
  if (name === "smtp") return "smtp";
  return "local";
}

function indexOrLast(preference: TransportName[], name: TransportName): number {
  const index = preference.indexOf(name);
  return index === -1 ? preference.length : index;
}
