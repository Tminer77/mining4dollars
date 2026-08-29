/**
 * The whole experiment, end to end, in one process.
 *
 *   node devtools/demo.ts
 *
 * It runs the scenario the project is judged by: a link becomes a
 * conversation, a third person joins the same link, a participant goes away
 * and messages reach them anyway over SMTP, and everyone converges when they
 * come back. Every claim it prints is checked; if a step does not actually
 * happen the demo exits non-zero.
 *
 * Nothing here is simulated. The peers talk over real WebSocket connections
 * on loopback, and the SMTP path goes through a real SMTP server that spools
 * to disk and retries.
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { LinkChatNode } from "../src/node/node.ts";
import { SmtpListenerSource } from "../src/transports/smtp/inbound.ts";
import { DevMta } from "./dev-mta.ts";
import { registerRoute } from "./dev-mail.ts";

const root = mkdtempSync(join(tmpdir(), "linkchat-demo-"));
const shared = join(root, "dev");
const MTA_PORT = 2540;

const ESC = String.fromCharCode(27);
const colour = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;
const paint = (code: string, text: string): string =>
  colour ? `${ESC}[${code}m${text}${ESC}[0m` : text;

const out = (line = ""): void => void process.stdout.write(`${line}\n`);
const step = (title: string): void => out(`\n${paint("1", title)}`);
const ok = (line: string): void => out(`  ${paint("32", "ok")}  ${line}`);
const info = (line: string): void => out(`      ${line}`);

let failures = 0;
function check(condition: boolean, description: string): void {
  if (condition) ok(description);
  else {
    failures += 1;
    out(`  ${paint("31", "FAILED")}  ${description}`);
  }
}

const mta = new DevMta({ port: MTA_PORT, sharedDir: shared, retryMs: 400 });

type Peer = {
  name: string;
  address: string;
  node: LinkChatNode;
  inbound: SmtpListenerSource;
};

function build(name: string, p2pPort: number, smtpPort: number, withP2P = true): Peer {
  const address = `${name.toLowerCase()}@linkchat.test`;
  const inbound = new SmtpListenerSource({ port: smtpPort, host: "127.0.0.1", addresses: [address] });
  const node = new LinkChatNode({
    displayName: name,
    dataDir: join(root, name.toLowerCase()),
    linkOrigin: "https://linkchat.local",
    p2p: withP2P ? { host: "127.0.0.1", port: p2pPort, advertiseHost: "127.0.0.1" } : false,
    smtp: {
      address,
      inbound,
      relay: { host: "127.0.0.1", port: MTA_PORT, secure: false, from: address, allowInsecure: true },
    },
    intervals: { syncMs: 1000, reconnectMs: 1500, outboxMs: 700, pingMs: 3000, deferredSyncMs: 4000 },
  });
  return { name, address, node, inbound };
}

async function startPeer(peer: Peer): Promise<void> {
  await peer.node.start();
  registerRoute(shared, { address: peer.address, host: "127.0.0.1", port: peer.inbound.port });
}

/** Poll a condition while driving the MTA, so the demo never sleeps blindly. */
async function until(label: string, condition: () => boolean, timeoutMs = 25_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) return true;
    await mta.flush();
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  info(`timed out waiting for ${label}`);
  return false;
}

const messagesOf = (node: LinkChatNode): string[] => {
  const conversation = node.conversations()[0];
  if (!conversation) return [];
  return node.view(conversation.id).state.messages.map((message) => `${message.senderName}: ${message.text}`);
};
const admittedCount = (node: LinkChatNode): number => {
  const conversation = node.conversations()[0];
  if (!conversation) return 0;
  return node.view(conversation.id).state.participants.filter((peer) => peer.admitted).length;
};
const routeEvents = (node: LinkChatNode): string[] =>
  node.diagnostics.events(400).filter((event) => event.scope === "route").map((event) => event.message);

try {
  await mta.start();
  out("LinkChat demo");
  info(`workspace ${root}`);
  info(`dev MTA   127.0.0.1:${MTA_PORT} (real SMTP, spools to disk, retries)`);

  // ---------------------------------------------------------------- act 1
  step("1. Alice creates a conversation and gets a link");
  const alice = build("Alice", 7411, 7511);
  await startPeer(alice);
  const { conversation, link } = alice.node.createConversation("The experiment");
  info(link);
  check(link.includes("/join/"), "the link is a /join/ link");
  check(link.includes("#"), "the secret material is in the fragment, not the path");

  step("2. Bob opens the link");
  const bob = build("Bob", 7412, 7512);
  await startPeer(bob);
  await bob.node.joinByLink(link);
  await until("Bob to be admitted", () => admittedCount(alice.node) === 2 && admittedCount(bob.node) === 2);
  check(admittedCount(alice.node) === 2, "Alice sees 2 admitted participants");
  check(admittedCount(bob.node) === 2, "Bob sees 2 admitted participants");
  check(
    alice.node.view(conversation.id).connection === "direct",
    "Alice reaches Bob directly (peer to peer, no server in the path)",
  );

  step("3. Carol opens the same link");
  const carol = build("Carol", 7413, 7513);
  await startPeer(carol);
  await carol.node.joinByLink(link);
  await until("Carol to be admitted everywhere", () =>
    [alice, bob, carol].every((peer) => admittedCount(peer.node) === 3));
  check(admittedCount(carol.node) === 3, "Carol sees all three participants");
  check(admittedCount(bob.node) === 3, "Bob learned about Carol without being sent the link");

  step("4. All three talk");
  await alice.node.post(conversation.id, "Hey!");
  await bob.node.post(bob.node.conversations()[0]!.id, "It works.");
  await carol.node.post(carol.node.conversations()[0]!.id, "I am in!");
  await until("messages to converge", () =>
    [alice, bob, carol].every((peer) => messagesOf(peer.node).length === 3));
  for (const peer of [alice, bob, carol]) {
    check(messagesOf(peer.node).length === 3, `${peer.name} has all three messages`);
  }
  check(
    JSON.stringify(messagesOf(alice.node)) === JSON.stringify(messagesOf(carol.node)),
    "every node shows the messages in the same order",
  );

  // ---------------------------------------------------------------- act 2
  step("5. Bob goes offline entirely");
  await bob.node.stop();
  await bob.inbound.stop();
  ok("Bob's node is stopped: no socket to dial, no mailbox to deliver to");

  step("6. Alice sends anyway");
  await alice.node.post(conversation.id, "Sent while you were away.");
  await until("the message to leave Alice over SMTP", () =>
    routeEvents(alice.node).some((message) => message.includes("via smtp")));
  check(
    routeEvents(alice.node).some((message) => message.includes("via smtp")),
    "P2P failed, so the router fell back to SMTP",
  );
  await until("the MTA to hold the message", () => mta.stats().queued > 0, 8000);
  check(mta.stats().queued > 0, "the MTA is holding mail for an unreachable Bob (store-and-forward)");
  info(`MTA queue: ${JSON.stringify(mta.stats())}`);

  step("7. Bob comes back, but only over SMTP");
  const bobSmtpOnly = build("Bob", 7412, 7512, false);
  await startPeer(bobSmtpOnly);
  await until("Bob to receive the stored message", () =>
    messagesOf(bobSmtpOnly.node).some((message) => message.includes("Sent while you were away")));
  check(
    messagesOf(bobSmtpOnly.node).some((message) => message.includes("Sent while you were away")),
    "the queued message reached Bob over SMTP once his node was back",
  );
  check(mta.stats().delivered > 0, `the MTA delivered ${mta.stats().delivered} message(s) in total`);

  await bobSmtpOnly.node.post(bobSmtpOnly.node.conversations()[0]!.id, "Got it, over mail.");
  await until("Alice to receive Bob's SMTP reply", () =>
    messagesOf(alice.node).some((message) => message.includes("over mail")));
  check(
    messagesOf(alice.node).some((message) => message.includes("over mail")),
    "Bob's reply travelled back over SMTP",
  );

  // ---------------------------------------------------------------- act 3
  step("8. Bob's direct connectivity returns");
  await bobSmtpOnly.node.stop();
  await bobSmtpOnly.inbound.stop();
  const bobBack = build("Bob", 7412, 7512, true);
  await startPeer(bobBack);
  const bobIsDirect = (): boolean =>
    alice.node
      .view(conversation.id)
      .routes.some((route) => route.peerId === bobBack.node.identity.peerId && route.state === "direct");
  await until("Alice to route to Bob directly again", bobIsDirect);
  check(bobIsDirect(), "Alice is back to a direct connection with Bob");

  step("9. Everyone converges");
  await alice.node.post(conversation.id, "Back on a direct connection.");
  await until("all nodes to hold six messages", () =>
    [alice.node, bobBack.node, carol.node].every((node) => messagesOf(node).length === 6));
  for (const [name, node] of [["Alice", alice.node], ["Bob", bobBack.node], ["Carol", carol.node]] as const) {
    check(messagesOf(node).length === 6, `${name} holds all six messages`);
  }
  const transcripts = [alice.node, bobBack.node, carol.node].map((node) => JSON.stringify(messagesOf(node)));
  check(new Set(transcripts).size === 1, "all three transcripts are identical");
  check(
    [alice.node, bobBack.node, carol.node].every((node) => node.conversations()[0]!.log.conflicts === 0),
    "no forked sequence numbers anywhere",
  );

  step("Final state");
  for (const [name, node] of [["Alice", alice.node], ["Bob", bobBack.node], ["Carol", carol.node]] as const) {
    const view = node.view(node.conversations()[0]!.id);
    out(`  ${name} (${node.identity.peerId})  transport: ${view.connection}`);
    for (const message of view.state.messages) info(`${message.senderName}: ${message.text}`);
  }
  const counters = alice.node.status().counters as Record<string, number>;
  out("");
  info(`Alice frames sent/received: ${counters.framesSent}/${counters.framesReceived}`);
  info(`duplicates suppressed: ${counters.duplicatesSuppressed}, records rejected: ${counters.recordsRejected}`);
  info(`MTA: ${JSON.stringify(mta.stats())}`);

  await Promise.all([alice.node.stop(), bobBack.node.stop(), carol.node.stop()]);
  await Promise.all([alice.inbound.stop(), bobBack.inbound.stop(), carol.inbound.stop()]);
  await mta.stop();
} finally {
  rmSync(root, { recursive: true, force: true });
}

out("");
if (failures > 0) {
  out(paint("31", `${failures} check(s) failed`));
  process.exit(1);
}
out(paint("32", "Every check passed."));
process.exit(0);
