/**
 * In-process transport.
 *
 * Every node registered on the same LocalBus can reach every other one
 * without a socket. This exists for two honest reasons: tests need a
 * transport with no ports and no timing, and the demo needs to show three
 * participants converging without asking the reader to trust the network
 * layer yet. It is not a stand-in for P2P and never claims to be one — the
 * diagnostics panel labels it "local (same process)".
 */
import type { SignedFrame, TransportName } from "../../protocol/types.ts";
import {
  TransportUnavailableError,
  type PeerAddress,
  type Transport,
  type TransportHandlers,
  type TransportStatus,
} from "../transport.ts";

export class LocalBus {
  readonly #nodes = new Map<string, LocalTransport>();

  register(peerId: string, transport: LocalTransport): void {
    this.#nodes.set(peerId, transport);
  }

  unregister(peerId: string): void {
    this.#nodes.delete(peerId);
  }

  reachable(peerId: string): boolean {
    return this.#nodes.get(peerId)?.online === true;
  }

  deliver(to: string, frame: SignedFrame): boolean {
    const target = this.#nodes.get(to);
    if (!target || !target.online) return false;
    target.receive(frame);
    return true;
  }
}

export class LocalTransport implements Transport {
  readonly name: TransportName = "local";
  readonly delivery = "immediate" as const;
  readonly #bus: LocalBus;
  readonly #peerId: string;
  #handlers: TransportHandlers | null = null;
  #running = false;
  #sent = 0;
  #received = 0;
  #failures = 0;

  constructor(bus: LocalBus, peerId: string) {
    this.#bus = bus;
    this.#peerId = peerId;
  }

  get online(): boolean {
    return this.#running;
  }

  attach(handlers: TransportHandlers): void {
    this.#handlers = handlers;
  }

  async start(): Promise<void> {
    this.#running = true;
    this.#bus.register(this.#peerId, this);
  }

  async stop(): Promise<void> {
    this.#running = false;
    this.#bus.unregister(this.#peerId);
  }

  canReach(peer: PeerAddress): boolean {
    return this.#running && this.#bus.reachable(peer.peerId);
  }

  async send(peer: PeerAddress, frame: SignedFrame): Promise<void> {
    if (!this.#running) {
      throw new TransportUnavailableError("local", "local transport is stopped");
    }
    // Deliver on a later tick so callers cannot accidentally depend on
    // synchronous delivery that no real transport would give them.
    await Promise.resolve();
    if (!this.#bus.deliver(peer.peerId, structuredClone(frame))) {
      this.#failures += 1;
      throw new TransportUnavailableError("local", `${peer.peerId} is not on this bus`);
    }
    this.#sent += 1;
  }

  receive(frame: SignedFrame): void {
    this.#received += 1;
    this.#handlers?.onFrame({ frame, transport: "local" });
  }

  status(): TransportStatus {
    return {
      name: "local",
      running: this.#running,
      detail: this.#running ? "local (same process)" : "stopped",
      framesSent: this.#sent,
      framesReceived: this.#received,
      failures: this.#failures,
    };
  }
}
