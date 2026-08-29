/**
 * Group sizes. These use the in-process bus so that ten participants do not
 * mean ten listening sockets; the protocol path (sign, encrypt, fan out,
 * dedupe, sync) is identical, and the socket path is covered by p2p.test.ts.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { LocalBus } from "../src/transports/local/local-transport.ts";
import type { LinkChatNode } from "../src/node/node.ts";
import { localNode, until } from "./helpers.ts";

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

async function group(size: number, t: { after: (fn: () => Promise<void>) => void }): Promise<LinkChatNode[]> {
  const bus = new LocalBus();
  const nodes = Array.from({ length: size }, (_, index) => localNode(`Peer${index + 1}`, bus));
  t.after(async () => {
    await Promise.all(nodes.map((node) => node.stop()));
  });
  await Promise.all(nodes.map((node) => node.start()));

  const [creator, ...rest] = nodes as [LinkChatNode, ...LinkChatNode[]];
  const { link } = creator.createConversation(`Group of ${size}`);
  // Everyone joins from the one link the creator produced - the same link,
  // shared onward, exactly as a person would.
  for (const node of rest) {
    await node.joinByLink(link);
    await until(() => admitted(node) >= 2, 10000, `${node.identity.displayName} admitted`);
  }
  await until(
    () => nodes.every((node) => admitted(node) === size),
    20000,
    `all ${size} participants known everywhere`,
  );
  return nodes;
}

for (const size of [2, 3, 10]) {
  test(`${size} participants all see each other and every message`, async (t) => {
    const nodes = await group(size, t);
    for (const node of nodes) {
      await node.post(node.conversations()[0]!.id, `hello from ${node.identity.displayName}`);
    }
    await until(
      () => nodes.every((node) => textsOf(node).length === size),
      25000,
      "every message on every node",
    );

    const transcripts = nodes.map((node) => JSON.stringify(textsOf(node)));
    assert.equal(new Set(transcripts).size, 1, "identical transcript on every node");
    assert.equal(
      nodes.every((node) => node.conversations()[0]!.log.conflicts === 0),
      true,
    );
  });
}

test("a message that reaches a node twice is shown once", async (t) => {
  const nodes = await group(3, t);
  const [alice, bob] = nodes as [LinkChatNode, LinkChatNode];
  const conversationId = alice.conversations()[0]!.id;
  await alice.post(conversationId, "only once please");
  await until(() => textsOf(bob).includes("only once please"), 10000, "delivery");

  // Re-deliver the same record by hand, as a relay flood legitimately would.
  const record = alice
    .conversations()[0]!
    .records()
    .find((entry) => entry.header.type === "text")!;
  const outcomes = [0, 1, 2].map(() => bob.conversations()[0]!.ingest(record).status);

  assert.deepEqual(outcomes, ["duplicate", "duplicate", "duplicate"]);
  assert.equal(textsOf(bob).filter((text) => text === "only once please").length, 1);
});
