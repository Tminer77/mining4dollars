/**
 * These run over real WebSocket connections on loopback: real sockets, real
 * handshakes, real framing. Nothing about the transport is stubbed.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { p2pNode, until } from "./helpers.ts";
import type { LinkChatNode } from "../src/node/node.ts";

const messagesOf = (node: LinkChatNode): string[] => {
  const conversation = node.conversations()[0];
  return conversation ? node.view(conversation.id).state.messages.map((message) => message.text) : [];
};
const admitted = (node: LinkChatNode): number => {
  const conversation = node.conversations()[0];
  return conversation
    ? node.view(conversation.id).state.participants.filter((peer) => peer.admitted).length
    : 0;
};

test("two peers connect directly and exchange messages", async (t) => {
  const alice = p2pNode("Alice");
  const bob = p2pNode("Bob");
  t.after(async () => {
    await alice.stop();
    await bob.stop();
  });
  await alice.start();
  await bob.start();

  const { conversation, link } = alice.createConversation("Direct");
  await bob.joinByLink(link);
  await until(() => admitted(alice) === 2 && admitted(bob) === 2, 8000, "mutual admission");

  const view = alice.view(conversation.id);
  assert.equal(view.connection, "direct");
  assert.equal(view.routes[0]!.state, "direct");

  await alice.post(conversation.id, "hello over a socket");
  await until(() => messagesOf(bob).includes("hello over a socket"), 8000, "delivery to Bob");
  assert.equal(alice.view(conversation.id).routes[0]!.lastTransport, "p2p");

  await bob.post(bob.conversations()[0]!.id, "and back");
  await until(() => messagesOf(alice).includes("and back"), 8000, "delivery to Alice");
});

test("a third participant joins the same link and everyone converges", async (t) => {
  const alice = p2pNode("Alice");
  const bob = p2pNode("Bob");
  const carol = p2pNode("Carol");
  t.after(async () => {
    await Promise.all([alice.stop(), bob.stop(), carol.stop()]);
  });
  await Promise.all([alice.start(), bob.start(), carol.start()]);

  const { conversation, link } = alice.createConversation("Group");
  await bob.joinByLink(link);
  await until(() => admitted(bob) === 2, 8000, "Bob admitted");
  await carol.joinByLink(link);

  await until(() => [alice, bob, carol].every((node) => admitted(node) === 3), 12000, "three participants");

  await alice.post(conversation.id, "one");
  await bob.post(bob.conversations()[0]!.id, "two");
  await carol.post(carol.conversations()[0]!.id, "three");
  await until(
    () => [alice, bob, carol].every((node) => messagesOf(node).length === 3),
    12000,
    "all messages everywhere",
  );

  const transcripts = [alice, bob, carol].map((node) => JSON.stringify(messagesOf(node)));
  assert.equal(new Set(transcripts).size, 1, "identical order on every node");
});

test("Bob relays to Carol without Carol ever reaching Alice", async (t) => {
  // Alice invites Bob. Bob invites Carol with a link that only advertises
  // Bob. Carol must still end up with Alice's messages, which can only have
  // arrived by Bob forwarding them.
  const alice = p2pNode("Alice");
  const bob = p2pNode("Bob");
  const carol = p2pNode("Carol");
  t.after(async () => {
    await Promise.all([alice.stop(), bob.stop(), carol.stop()]);
  });
  await Promise.all([alice.start(), bob.start(), carol.start()]);

  const { conversation, link } = alice.createConversation("Relay");
  await bob.joinByLink(link);
  await until(() => admitted(bob) === 2, 8000, "Bob admitted");
  await alice.post(conversation.id, "from Alice");
  await until(() => messagesOf(bob).includes("from Alice"), 8000, "Bob has Alice's message");

  const bobConversation = bob.conversations()[0]!;
  const bobOnly = bobConversation.createInvite({ hints: bobConversation.selfHints }).link;
  await carol.joinByLink(bobOnly);
  await until(() => messagesOf(carol).includes("from Alice"), 12000, "Carol synced through Bob");
  assert.equal(admitted(carol), 3);
});

test("a peer that goes away is reported offline, then synchronises on return", async (t) => {
  const alice = p2pNode("Alice");
  let bob = p2pNode("Bob");
  t.after(async () => {
    await alice.stop();
    await bob.stop();
  });
  await alice.start();
  await bob.start();

  const { conversation, link } = alice.createConversation("Resync");
  await bob.joinByLink(link);
  await until(() => admitted(alice) === 2, 8000, "Bob admitted");

  const bobPeerId = bob.identity.peerId;
  const bobPort = (bob.selfHints().find((hint) => hint.kind === "p2p") as { url: string }).url;
  await bob.stop();
  await until(
    () => alice.view(conversation.id).routes.every((route) => route.state !== "direct"),
    8000,
    "Alice notices Bob is gone",
  );

  await alice.post(conversation.id, "while you were out");
  assert.equal(messagesOf(alice).length, 1);

  // Same identity, same address: Bob really is the same peer coming back.
  bob = p2pNode("Bob", {
    p2p: {
      host: "127.0.0.1",
      port: Number(bobPort.split(":")[2]),
      advertiseHost: "127.0.0.1",
    },
  });
  await bob.start();
  await bob.joinByLink(link);
  await until(() => messagesOf(bob).includes("while you were out"), 12000, "Bob catches up");
  assert.notEqual(bob.identity.peerId, bobPeerId, "an ephemeral node is a new identity");
});
