import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalJson } from "../src/crypto/encoding.ts";
import { Identity } from "../src/identity/identity.ts";
import { ProtocolError } from "../src/protocol/errors.ts";
import { ReplayGuard, signFrame, verifyFrame } from "../src/protocol/frames.ts";
import { buildRecord, compareRecords, openRecord, verifyRecord } from "../src/protocol/records.ts";
import { randomKey } from "../src/crypto/primitives.ts";

const key = randomKey();
const alice = Identity.generate("Alice");
const CONVERSATION = "c_0123456789ABCDEFGHJKMNPQRS";

const record = (overrides: Partial<Parameters<typeof buildRecord>[0]> = {}) =>
  buildRecord({
    identity: alice,
    conversationId: CONVERSATION,
    conversationKey: key,
    type: "text",
    payload: { text: "hello" },
    seq: 1,
    lamport: 1,
    ...overrides,
  });

test("canonical JSON is key-order independent", () => {
  assert.equal(canonicalJson({ b: 1, a: 2 }), canonicalJson({ a: 2, b: 1 }));
  assert.equal(canonicalJson({ a: { d: 1, c: 2 } }), '{"a":{"c":2,"d":1}}');
});

test("canonical JSON refuses values that would serialise ambiguously", () => {
  assert.throws(() => canonicalJson({ n: Number.NaN }));
  assert.throws(() => canonicalJson({ n: Number.POSITIVE_INFINITY }));
});

test("a record verifies and decrypts", () => {
  const signed = record();
  verifyRecord(signed, { conversationId: CONVERSATION });
  assert.deepEqual(openRecord(signed, key), { text: "hello" });
});

test("editing any header field breaks the signature", () => {
  for (const mutate of [
    (r: ReturnType<typeof record>) => (r.header.seq = 9),
    (r: ReturnType<typeof record>) => (r.header.timestamp = new Date(0).toISOString()),
    (r: ReturnType<typeof record>) => (r.header.sender_id = "p_AAAAAAAAAAAAAAAA"),
    (r: ReturnType<typeof record>) => (r.body.ciphertext = "AAAA"),
  ]) {
    const signed = record();
    mutate(signed);
    assert.throws(() => verifyRecord(signed), ProtocolError);
  }
});

test("a record cannot be replayed into another conversation", () => {
  const signed = record();
  assert.throws(
    () => verifyRecord(signed, { conversationId: "c_ZZZZZZZZZZZZZZZZZZZZZZZZZZ" }),
    (error: ProtocolError) => error.code === "wrong_conversation",
  );
});

test("a peer id that does not match its key is rejected", () => {
  const signed = record();
  const mallory = Identity.generate("Mallory");
  signed.header.sender_key = Buffer.from(mallory.publicKey).toString("base64url");
  assert.throws(() => verifyRecord(signed), (error: ProtocolError) => error.code === "bad_peer_id");
});

test("a far-future record is rejected", () => {
  const signed = record({ now: Date.now() + 60 * 60 * 1000 });
  assert.throws(() => verifyRecord(signed), (error: ProtocolError) => error.code === "future_dated");
});

test("an old record is accepted, because store-and-forward is slow", () => {
  const signed = record({ now: Date.now() - 5 * 24 * 60 * 60 * 1000 });
  verifyRecord(signed);
});

test("the wrong conversation key cannot open a body", () => {
  const signed = record();
  assert.throws(
    () => openRecord(signed, randomKey()),
    (error: ProtocolError) => error.code === "undecryptable",
  );
});

test("record order is total and identical regardless of arrival order", () => {
  const bob = Identity.generate("Bob");
  const records = [
    record({ seq: 1, lamport: 1 }),
    record({ identity: bob, seq: 1, lamport: 2 }),
    record({ seq: 2, lamport: 3 }),
  ];
  const forward = [...records].sort(compareRecords).map((r) => r.header.message_id);
  const backward = [...records].reverse().sort(compareRecords).map((r) => r.header.message_id);
  assert.deepEqual(forward, backward);
});

test("frames verify, and a replayed frame is rejected once", () => {
  const guard = new ReplayGuard();
  const frame = signFrame(alice, { kind: "ack", conversation_id: CONVERSATION, message_ids: [] });
  assert.equal(verifyFrame(frame, { replayGuard: guard }).kind, "ack");
  assert.throws(
    () => verifyFrame(frame, { replayGuard: guard }),
    (error: ProtocolError) => error.code === "replayed",
  );
});

test("a frame older than the replay window is rejected", () => {
  const frame = signFrame(alice, { kind: "ack", conversation_id: CONVERSATION, message_ids: [] });
  assert.throws(
    () => verifyFrame(frame, { now: Date.now() + 8 * 24 * 60 * 60 * 1000 }),
    (error: ProtocolError) => error.code === "stale",
  );
});

test("a tampered frame body is rejected", () => {
  const frame = signFrame(alice, { kind: "ack", conversation_id: CONVERSATION, message_ids: [] });
  (frame.frame as { message_ids: string[] }).message_ids = ["m_injected"];
  assert.throws(() => verifyFrame(frame), (error: ProtocolError) => error.code === "bad_signature");
});
