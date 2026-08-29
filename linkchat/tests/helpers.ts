/** Shared fixtures. Nothing here mocks the protocol - only the wiring. */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { LinkChatNode, type NodeConfig } from "../src/node/node.ts";
import { LocalBus } from "../src/transports/local/local-transport.ts";
import { Conversation } from "../src/conversation/conversation.ts";
import { Identity } from "../src/identity/identity.ts";
import { MemoryStorage } from "../src/storage/memory-storage.ts";

export const wait = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/** Poll until true, so tests wait on facts rather than on the clock. */
export async function until(
  condition: () => boolean,
  timeoutMs = 8000,
  label = "condition",
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) return;
    await wait(25);
  }
  throw new Error(`timed out waiting for ${label}`);
}

export function tempDir(prefix = "linkchat-test-"): { path: string; cleanup: () => void } {
  const path = mkdtempSync(join(tmpdir(), prefix));
  return { path, cleanup: () => rmSync(path, { recursive: true, force: true }) };
}

export function localNode(name: string, bus: LocalBus, overrides: Partial<NodeConfig> = {}): LinkChatNode {
  return new LinkChatNode({
    displayName: name,
    ephemeral: true,
    p2p: false,
    smtp: false,
    localBus: bus,
    intervals: { syncMs: 200, reconnectMs: 100000, outboxMs: 200, pingMs: 100000 },
    ...overrides,
  });
}

export function p2pNode(name: string, overrides: Partial<NodeConfig> = {}): LinkChatNode {
  return new LinkChatNode({
    displayName: name,
    ephemeral: true,
    p2p: { host: "127.0.0.1", port: 0, advertiseHost: "127.0.0.1" },
    smtp: false,
    intervals: { syncMs: 300, reconnectMs: 500, outboxMs: 300, pingMs: 100000 },
    ...overrides,
  });
}

/** A conversation pair with no transports at all, for pure protocol tests. */
export function offlinePair(): {
  alice: Identity;
  bob: Identity;
  aliceConv: Conversation;
  link: string;
  joinBob: () => Conversation;
} {
  const alice = Identity.generate("Alice");
  const bob = Identity.generate("Bob");
  const aliceStorage = new MemoryStorage();
  const aliceConv = Conversation.create({ identity: alice, storage: aliceStorage }, { title: "Test" });
  const { link } = aliceConv.createInvite({});
  return {
    alice,
    bob,
    aliceConv,
    link,
    joinBob: () =>
      Conversation.joinFromLink({ identity: bob, storage: new MemoryStorage() }, { link }).conversation,
  };
}

/** Copy every record from one conversation into another, both ways. */
export function reconcile(...conversations: Conversation[]): void {
  for (const source of conversations) {
    for (const target of conversations) {
      if (source === target) continue;
      for (const record of source.records()) {
        try {
          target.ingest(record);
        } catch {
          // Rejections are the subject of their own tests.
        }
      }
    }
  }
}
