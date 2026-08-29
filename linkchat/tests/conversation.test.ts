import assert from "node:assert/strict";
import { test } from "node:test";
import { Conversation } from "../src/conversation/conversation.ts";
import { Identity } from "../src/identity/identity.ts";
import { MessageLog } from "../src/messages/log.ts";
import { ProtocolError } from "../src/protocol/errors.ts";
import { buildRecord } from "../src/protocol/records.ts";
import { MemoryStorage } from "../src/storage/memory-storage.ts";
import { randomKey } from "../src/crypto/primitives.ts";
import { offlinePair, reconcile } from "./helpers.ts";

test("creating a conversation writes a genesis record and admits the creator", () => {
  const { aliceConv } = offlinePair();
  const state = aliceConv.state();
  assert.equal(state.participants.length, 1);
  assert.equal(state.participants[0]!.isCreator, true);
  assert.equal(state.participants[0]!.admitted, true);
  assert.equal(state.creatorId, aliceConv.state().participants[0]!.peerId);
  assert.ok(aliceConv.inviteSecret);
});

test("joining by link converges both sides on the same participants", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);

  for (const conversation of [aliceConv, bobConv]) {
    const names = conversation.state().participants.map((peer) => peer.displayName).sort();
    assert.deepEqual(names, ["Alice", "Bob"]);
    assert.ok(conversation.state().participants.every((peer) => peer.admitted));
  }
});

test("a joiner learns the invite secret and can invite in turn", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  assert.equal(bobConv.inviteSecret, null, "not before syncing");
  reconcile(aliceConv, bobConv);
  assert.ok(bobConv.inviteSecret, "after syncing");

  const carol = Identity.generate("Carol");
  const carolConv = Conversation.joinFromLink(
    { identity: carol, storage: new MemoryStorage() },
    { link: bobConv.createInvite({}).link },
  ).conversation;
  reconcile(aliceConv, bobConv, carolConv);
  assert.equal(aliceConv.state().participants.filter((peer) => peer.admitted).length, 3);
});

test("a join with a forged invite is stored but never admitted", () => {
  const { aliceConv, link } = offlinePair();
  const mallory = Identity.generate("Mallory");
  const parsed = new URL(link.replace("#", "?"));
  const forged = link.replace(parsed.searchParams.get("m")!, Buffer.alloc(32, 9).toString("base64url"));

  const malloryConv = Conversation.joinFromLink(
    { identity: mallory, storage: new MemoryStorage() },
    { link: forged },
  ).conversation;
  reconcile(aliceConv, malloryConv);

  const seen = aliceConv.state().participants.find((peer) => peer.displayName === "Mallory");
  assert.ok(seen, "the join record is kept as evidence");
  assert.equal(seen.admitted, false);
  assert.equal(seen.admissionError, "invite_invalid");
});

test("a revoked invite stops admitting new joins", () => {
  const { aliceConv, link } = offlinePair();
  const parsed = new URL(link.replace("#", "?"));
  aliceConv.revokeInvite(parsed.searchParams.get("n")!);

  const late = Identity.generate("Late");
  const lateConv = Conversation.joinFromLink(
    { identity: late, storage: new MemoryStorage() },
    { link },
  ).conversation;
  reconcile(aliceConv, lateConv);

  const seen = aliceConv.state().participants.find((peer) => peer.displayName === "Late");
  assert.equal(seen?.admitted, false);
  assert.equal(seen?.admissionError, "invite was revoked");
});

test("an already-joined participant is not ejected by a later revocation", () => {
  const { aliceConv, joinBob, link } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);
  const nonce = new URL(link.replace("#", "?")).searchParams.get("n")!;
  aliceConv.revokeInvite(nonce);
  reconcile(aliceConv, bobConv);

  const bob = aliceConv.state().participants.find((peer) => peer.displayName === "Bob");
  assert.equal(bob?.admitted, true);
});

test("live admission uses the local clock, so a backdated join cannot sneak in", () => {
  const { aliceConv, link } = offlinePair();
  const invite = new URL(link.replace("#", "?"));
  const proof = {
    nonce: invite.searchParams.get("n")!,
    exp: Number(invite.searchParams.get("e")),
    mac: invite.searchParams.get("m")!,
  };
  assert.equal(aliceConv.admits(proof).ok, true);
  assert.equal(aliceConv.admits(proof, proof.exp + 1).ok, false);
});

test("an expired link is refused at join time", () => {
  const alice = Identity.generate("Alice");
  const conversation = Conversation.create({ identity: alice, storage: new MemoryStorage() }, {});
  const { link } = conversation.createInvite({ ttlMs: 10 });
  assert.throws(
    () =>
      Conversation.joinFromLink(
        { identity: Identity.generate("Bob"), storage: new MemoryStorage() },
        { link, now: Date.now() + 1000 },
      ),
    (error: ProtocolError) => error.code === "invite_expired",
  );
});

test("records encrypted to a different conversation key are refused", () => {
  const { aliceConv } = offlinePair();
  const outsider = Identity.generate("Outsider");
  const foreign = buildRecord({
    identity: outsider,
    conversationId: aliceConv.id,
    conversationKey: randomKey(),
    type: "text",
    payload: { text: "let me in" },
    seq: 1,
    lamport: 1,
  });
  assert.throws(() => aliceConv.ingest(foreign), (error: ProtocolError) => error.code === "undecryptable");
});

test("the same record ingested twice appears once", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  const hello = aliceConv.post("hello");
  assert.equal(bobConv.ingest(hello).status, "added");
  assert.equal(bobConv.ingest(hello).status, "duplicate");
  assert.equal(bobConv.state().messages.filter((message) => message.text === "hello").length, 1);
});

test("the log detects a sender that reuses a sequence number", () => {
  const alice = Identity.generate("Alice");
  const key = randomKey();
  const log = new MessageLog("c_x");
  const make = (text: string) =>
    buildRecord({
      identity: alice,
      conversationId: "c_x",
      conversationKey: key,
      type: "text",
      payload: { text },
      seq: 1,
      lamport: 1,
    });
  assert.equal(log.append(make("one")).status, "added");
  assert.equal(log.append(make("two")).status, "conflict");
  assert.equal(log.conflicts, 1);
});

test("watermarks describe exactly what a peer is missing", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);
  aliceConv.post("one");
  aliceConv.post("two");

  const missing = aliceConv.missingFor(bobConv.watermarks());
  assert.deepEqual(
    missing.map((record) => record.header.type),
    ["text", "text"],
  );
  for (const record of missing) bobConv.ingest(record);
  assert.equal(aliceConv.missingFor(bobConv.watermarks()).length, 0);
});

test("everyone computes the same message order from the same records", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);
  aliceConv.post("a1");
  bobConv.post("b1");
  aliceConv.post("a2");
  reconcile(aliceConv, bobConv);

  assert.deepEqual(
    aliceConv.state().messages.map((message) => message.text),
    bobConv.state().messages.map((message) => message.text),
  );
  assert.equal(aliceConv.state().messages.length, 3);
});
