import assert from "node:assert/strict";
import { test } from "node:test";
import { Identity } from "../src/identity/identity.ts";
import { signFrame } from "../src/protocol/frames.ts";
import type { SignedFrame, TransportName } from "../src/protocol/types.ts";
import { LocalBus, LocalTransport } from "../src/transports/local/local-transport.ts";
import { TransportRouter } from "../src/transports/router.ts";
import {
  TransportUnavailableError,
  type PeerAddress,
  type Transport,
  type TransportHandlers,
  type TransportStatus,
} from "../src/transports/transport.ts";

const alice = Identity.generate("Alice");
const frame = (): SignedFrame => signFrame(alice, { kind: "ack", conversation_id: "c_x", message_ids: [] });

/** A transport whose reachability and failures the test controls exactly. */
class FakeTransport implements Transport {
  readonly delivery = "immediate" as const;
  reachable = true;
  failing = false;
  readonly delivered: string[] = [];
  #handlers: TransportHandlers | null = null;
  readonly name: TransportName;

  constructor(name: TransportName) {
    this.name = name;
  }

  attach(handlers: TransportHandlers): void {
    this.#handlers = handlers;
  }
  async start(): Promise<void> {}
  async stop(): Promise<void> {}
  canReach(): boolean {
    return this.reachable;
  }
  async send(peer: PeerAddress): Promise<void> {
    if (this.failing) throw new TransportUnavailableError(this.name, "simulated failure");
    this.delivered.push(peer.peerId);
  }
  status(): TransportStatus {
    return {
      name: this.name,
      running: true,
      detail: "fake",
      framesSent: this.delivered.length,
      framesReceived: 0,
      failures: 0,
    };
  }
  deliverInbound(signed: SignedFrame): void {
    this.#handlers?.onFrame({ frame: signed, transport: this.name });
  }
}

test("the local bus delivers between registered nodes", async () => {
  const bus = new LocalBus();
  const a = new LocalTransport(bus, "p_A");
  const b = new LocalTransport(bus, "p_B");
  const received: SignedFrame[] = [];
  b.attach({ onFrame: (inbound) => received.push(inbound.frame) });
  await a.start();
  await b.start();

  await a.send({ peerId: "p_B", hints: [] }, frame());
  assert.equal(received.length, 1);

  await b.stop();
  await assert.rejects(() => a.send({ peerId: "p_B", hints: [] }, frame()), TransportUnavailableError);
});

test("the router prefers P2P and falls back to SMTP", async () => {
  const p2p = new FakeTransport("p2p");
  const smtp = new FakeTransport("smtp");
  const router = new TransportRouter({ transports: [smtp, p2p] });
  const peer: PeerAddress = { peerId: "p_B", hints: [] };

  const direct = await router.send(peer, frame());
  assert.equal(direct.transport, "p2p");
  assert.equal(router.route(peer).state, "direct");

  p2p.reachable = false;
  const fallback = await router.send(peer, frame());
  assert.equal(fallback.transport, "smtp");
  assert.equal(router.route(peer).state, "smtp");
  assert.deepEqual(p2p.delivered, ["p_B"]);
  assert.deepEqual(smtp.delivered, ["p_B"]);
});

test("a transport that claims reachability but fails falls through to the next", async () => {
  const p2p = new FakeTransport("p2p");
  const smtp = new FakeTransport("smtp");
  p2p.failing = true;
  const router = new TransportRouter({ transports: [p2p, smtp] });

  const result = await router.send({ peerId: "p_B", hints: [] }, frame());
  assert.equal(result.transport, "smtp");
  assert.equal(result.ok, true);
});

test("with no transport available the frame is queued, then sent on retry", async () => {
  const p2p = new FakeTransport("p2p");
  p2p.reachable = false;
  const router = new TransportRouter({ transports: [p2p], baseBackoffMs: 0 });
  const peer: PeerAddress = { peerId: "p_B", hints: [] };

  const queued = await router.send(peer, frame());
  assert.equal(queued.ok, false);
  assert.equal(queued.queued, true);
  assert.equal(router.outboxSize, 1);
  assert.equal(router.route(peer).state, "offline");

  const stillDown = await router.flushOutbox();
  assert.equal(stillDown.delivered, 0);
  assert.equal(router.outboxSize, 1);

  p2p.reachable = true;
  const recovered = await router.flushOutbox();
  assert.equal(recovered.delivered, 1);
  assert.equal(router.outboxSize, 0);
  assert.deepEqual(p2p.delivered, ["p_B"]);
});

test("the outbox gives up after the attempt limit rather than growing forever", async () => {
  const p2p = new FakeTransport("p2p");
  p2p.reachable = false;
  const router = new TransportRouter({ transports: [p2p], baseBackoffMs: 0, maxAttempts: 3 });
  await router.send({ peerId: "p_B", hints: [] }, frame());
  for (let attempt = 0; attempt < 5; attempt += 1) await router.flushOutbox();
  assert.equal(router.outboxSize, 0);
  assert.equal(router.droppedCount, 1);
});

test("a broadcast reports per-peer outcomes independently", async () => {
  const p2p = new FakeTransport("p2p");
  const router = new TransportRouter({ transports: [p2p] });
  const results = await router.broadcast(
    [
      { peerId: "p_B", hints: [] },
      { peerId: "p_C", hints: [] },
    ],
    frame(),
  );
  assert.equal(results.size, 2);
  assert.ok([...results.values()].every((result) => result.ok));
});
