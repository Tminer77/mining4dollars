/**
 * Offline behaviour and convergence. A conversation is a replicated log, so
 * "you were offline" and "your message took the slow transport" are the same
 * problem, and these tests are the proof that it is solved once.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { join } from "node:path";
import { LinkChatNode } from "../src/node/node.ts";
import { LocalBus } from "../src/transports/local/local-transport.ts";
import { buildSyncResponse, buildSyncRequest } from "../src/sync/sync-engine.ts";
import { localNode, offlinePair, reconcile, tempDir, until } from "./helpers.ts";

const textsOf = (node: LinkChatNode): string[] => {
  const conversation = node.conversations()[0];
  return conversation ? node.view(conversation.id).state.messages.map((message) => message.text) : [];
};
const admitted = (node: LinkChatNode): number => {
  const conversation = node.conversations()[0];
  return conversation
    ? node.view(conversation.id).state.participants.filter((peer) => peer.admitted).length
    : 0;
};

test("a sync response carries exactly what the peer lacks", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);
  aliceConv.post("one");
  aliceConv.post("two");

  const response = buildSyncResponse(aliceConv, buildSyncRequest(bobConv));
  assert.equal(response.records.length, 2);
  assert.equal(response.complete, true);

  for (const record of response.records) bobConv.ingest(record);
  assert.equal(buildSyncResponse(aliceConv, buildSyncRequest(bobConv)).records.length, 0);
});

test("a large gap is delivered in batches, and the peer is told to ask again", () => {
  const { aliceConv, joinBob } = offlinePair();
  const bobConv = joinBob();
  reconcile(aliceConv, bobConv);
  for (let index = 0; index < 5; index += 1) aliceConv.post(`m${index}`);

  const first = buildSyncResponse(aliceConv, buildSyncRequest(bobConv), 2);
  assert.equal(first.records.length, 2);
  assert.equal(first.complete, false);
  for (const record of first.records) bobConv.ingest(record);

  const second = buildSyncResponse(aliceConv, buildSyncRequest(bobConv), 2);
  assert.equal(second.records.length, 2);
  assert.equal(second.complete, false);
});

test("a node that was away catches up when it returns", async (t) => {
  const bus = new LocalBus();
  const dir = tempDir();
  t.after(() => dir.cleanup());

  const alice = localNode("Alice", bus, { ephemeral: false, dataDir: join(dir.path, "alice") });
  let bob = localNode("Bob", bus, { ephemeral: false, dataDir: join(dir.path, "bob") });
  await alice.start();
  await bob.start();

  const { conversation, link } = alice.createConversation("Catch up");
  await bob.joinByLink(link);
  await until(() => admitted(alice) === 2 && admitted(bob) === 2, 8000, "admission");

  await alice.post(conversation.id, "before you left");
  await until(() => textsOf(bob).includes("before you left"), 8000, "first delivery");

  await bob.stop();
  await alice.post(conversation.id, "while you were away 1");
  await alice.post(conversation.id, "while you were away 2");
  assert.equal(textsOf(alice).length, 3);

  // Same data directory, so the same identity and the same log come back.
  bob = localNode("Bob", bus, { ephemeral: false, dataDir: join(dir.path, "bob") });
  t.after(async () => {
    await alice.stop();
    await bob.stop();
  });
  await bob.start();
  assert.deepEqual(textsOf(bob), ["before you left"], "the stored log survived the restart");

  await until(() => textsOf(bob).length === 3, 12000, "Bob to catch up");
  assert.deepEqual(textsOf(bob), textsOf(alice));
});

test("a participant joining late receives the whole history", async (t) => {
  const bus = new LocalBus();
  const alice = localNode("Alice", bus);
  const bob = localNode("Bob", bus);
  const carol = localNode("Carol", bus);
  t.after(async () => {
    await Promise.all([alice.stop(), bob.stop(), carol.stop()]);
  });
  await Promise.all([alice.start(), bob.start(), carol.start()]);

  const { conversation, link } = alice.createConversation("History");
  await bob.joinByLink(link);
  await until(() => admitted(bob) === 2, 8000, "Bob admitted");
  for (const text of ["one", "two", "three"]) await alice.post(conversation.id, text);
  await until(() => textsOf(bob).length === 3, 8000, "Bob current");

  await carol.joinByLink(link);
  await until(() => textsOf(carol).length === 3, 12000, "Carol backfilled");
  assert.deepEqual(textsOf(carol), ["one", "two", "three"]);
});

test("conversations and their key material survive a restart", async (t) => {
  const bus = new LocalBus();
  const dir = tempDir();
  t.after(() => dir.cleanup());

  const first = localNode("Alice", bus, { ephemeral: false, dataDir: join(dir.path, "alice") });
  await first.start();
  const { conversation } = first.createConversation("Persistent");
  await first.post(conversation.id, "written to disk");
  const peerId = first.identity.peerId;
  await first.stop();

  const second = localNode("Alice", bus, { ephemeral: false, dataDir: join(dir.path, "alice") });
  t.after(async () => second.stop());
  await second.start();

  assert.equal(second.identity.peerId, peerId);
  assert.deepEqual(textsOf(second), ["written to disk"]);
  // Minting an invite requires the conversation key and the invite secret,
  // so a link that still works proves both were restored.
  assert.match(second.createInvite(conversation.id).link, /\/join\/c_/);
});
