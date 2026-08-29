/**
 * Real SMTP throughout: a real submission to a real server, a real spool, and
 * a real relay onward to the recipient's own SMTP listener.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Identity } from "../src/identity/identity.ts";
import { signFrame } from "../src/protocol/frames.ts";
import { LINKCHAT_MEDIA_TYPE } from "../src/protocol/types.ts";
import { frameToMail, rawMailToFrame } from "../src/transports/smtp/mime.ts";
import { MaildirSource, SmtpListenerSource } from "../src/transports/smtp/inbound.ts";
import { SmtpTransport, relayFromEnv } from "../src/transports/smtp/smtp-transport.ts";
import { LinkChatNode } from "../src/node/node.ts";
import { DevMta } from "../devtools/dev-mta.ts";
import { registerRoute } from "../devtools/dev-mail.ts";
import { until, wait } from "./helpers.ts";

const MTA_PORT = 2551;
const alice = Identity.generate("Alice");

test("a frame survives a MIME round trip", async () => {
  const frame = signFrame(alice, { kind: "ack", conversation_id: "c_x", message_ids: ["m_1"] });
  const mail = frameToMail(frame);
  assert.equal(mail.contentType, LINKCHAT_MEDIA_TYPE);
  assert.match(mail.headers["X-LinkChat-Protocol"]!, /^linkchat\/1$/);

  // Assemble the message the way a mail client would, then parse it back.
  const raw = [
    "From: alice@linkchat.test",
    "To: bob@linkchat.test",
    `Subject: ${mail.subject}`,
    "MIME-Version: 1.0",
    'Content-Type: multipart/mixed; boundary="b"',
    "",
    "--b",
    "Content-Type: text/plain",
    "",
    mail.text,
    "--b",
    `Content-Type: ${mail.contentType}; name="linkchat.json"`,
    "Content-Disposition: attachment; filename=linkchat.json",
    "",
    mail.content.toString("utf8"),
    "--b--",
    "",
  ].join("\r\n");

  const parsed = await rawMailToFrame(raw);
  assert.deepEqual(parsed, frame);
});

test("ordinary email is ignored rather than treated as protocol traffic", async () => {
  const raw = ["From: someone@example.com", "To: bob@linkchat.test", "Subject: hi", "", "hello"].join("\r\n");
  assert.equal(await rawMailToFrame(raw), null);
});

test("relay configuration comes from the environment, and defaults to nothing", () => {
  assert.equal(relayFromEnv({}), null);
  assert.equal(relayFromEnv({ LINKCHAT_SMTP_HOST: "smtp.example" }), null, "a host alone is not enough");
  const relay = relayFromEnv({
    LINKCHAT_SMTP_HOST: "smtp.example",
    LINKCHAT_SMTP_FROM: "me@example",
    LINKCHAT_SMTP_USER: "u",
    LINKCHAT_SMTP_PASS: "p",
  });
  assert.equal(relay?.port, 587, "submission port by default");
  assert.deepEqual(relay?.auth, { user: "u", pass: "p" });
});

test("a maildir source picks up messages a delivery agent dropped", async () => {
  const dir = mkdtempSync(join(tmpdir(), "linkchat-maildir-"));
  try {
    const frame = signFrame(alice, { kind: "ack", conversation_id: "c_x", message_ids: ["m_2"] });
    const mail = frameToMail(frame);
    mkdirSync(join(dir, "new"), { recursive: true });
    writeFileSync(
      join(dir, "new", "1.eml"),
      [
        "From: alice@linkchat.test",
        "To: bob@linkchat.test",
        "MIME-Version: 1.0",
        `Content-Type: ${mail.contentType}`,
        "",
        mail.content.toString("utf8"),
        "",
      ].join("\r\n"),
    );

    const received: string[] = [];
    const source = new MaildirSource({ path: dir, pollMs: 50 });
    await source.start((inbound) => received.push(inbound.frame.kind));
    await until(() => received.length === 1, 4000, "maildir pickup");
    await source.stop();
    assert.deepEqual(received, ["ack"]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("two nodes talk over SMTP alone, including while one is offline", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "linkchat-smtp-"));
  const shared = join(root, "dev");
  const mta = new DevMta({ port: MTA_PORT, sharedDir: shared, retryMs: 300 });
  await mta.start();

  const build = (name: string) => {
    const address = `${name.toLowerCase()}@linkchat.test`;
    const inbound = new SmtpListenerSource({ port: 0, host: "127.0.0.1", addresses: [address] });
    const node = new LinkChatNode({
      displayName: name,
      dataDir: join(root, name.toLowerCase()),
      // No P2P at all: this test proves SMTP can carry the whole protocol.
      p2p: false,
      smtp: {
        address,
        inbound,
        relay: { host: "127.0.0.1", port: MTA_PORT, secure: false, from: address, allowInsecure: true },
      },
      intervals: { syncMs: 800, reconnectMs: 100000, outboxMs: 400, pingMs: 100000, deferredSyncMs: 1500 },
    });
    return { node, inbound, address };
  };

  const start = async (peer: ReturnType<typeof build>): Promise<void> => {
    await peer.node.start();
    registerRoute(shared, { address: peer.address, host: "127.0.0.1", port: peer.inbound.port });
  };

  const pump = async (condition: () => boolean, label: string, timeoutMs = 20000): Promise<void> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (condition()) return;
      await mta.flush();
      await wait(150);
    }
    throw new Error(`timed out waiting for ${label}`);
  };

  const a = build("Alice");
  const b = build("Bob");
  t.after(async () => {
    await Promise.all([a.node.stop(), b.node.stop()]);
    await Promise.all([a.inbound.stop(), b.inbound.stop()]);
    await mta.stop();
    rmSync(root, { recursive: true, force: true });
  });

  await start(a);
  await start(b);

  const { conversation, link } = a.node.createConversation("Mail only");
  await b.node.joinByLink(link);

  const textsOf = (node: LinkChatNode): string[] => {
    const held = node.conversations()[0];
    return held ? node.view(held.id).state.messages.map((message) => message.text) : [];
  };
  const admitted = (node: LinkChatNode): number => {
    const held = node.conversations()[0];
    return held ? node.view(held.id).state.participants.filter((peer) => peer.admitted).length : 0;
  };

  await pump(() => admitted(a.node) === 2 && admitted(b.node) === 2, "admission over SMTP");
  assert.equal(a.node.view(conversation.id).connection, "smtp");

  await a.node.post(conversation.id, "carried by mail");
  await pump(() => textsOf(b.node).includes("carried by mail"), "delivery over SMTP");

  // Now the interesting part: Bob's mailbox goes down entirely.
  await b.inbound.stop();
  await b.node.stop();
  await a.node.post(conversation.id, "while the mailbox was shut");
  await pump(() => mta.stats().queued > 0, "the MTA to hold the message", 10000);
  assert.ok(mta.stats().queued > 0, "an unreachable recipient means a held message, not a lost one");

  const bBack = build("Bob");
  await start(bBack);
  await pump(
    () => textsOf(bBack.node).includes("while the mailbox was shut"),
    "delivery after the recipient returned",
  );
  assert.deepEqual(textsOf(bBack.node), ["carried by mail", "while the mailbox was shut"]);
  await bBack.node.stop();
  await bBack.inbound.stop();
});

test("a relay that refuses is reported as a failure, never as a delivery", async () => {
  const transport = new SmtpTransport({
    address: "alice@linkchat.test",
    // Nothing is listening here.
    relay: { host: "127.0.0.1", port: 9, secure: false, from: "alice@linkchat.test", allowInsecure: true },
  });
  await transport.start();
  const check = await transport.verifyRelay();
  assert.equal(check.ok, false);
  await assert.rejects(() =>
    transport.send(
      { peerId: "p_B", hints: [{ kind: "smtp", address: "bob@linkchat.test" }] },
      signFrame(alice, { kind: "ack", conversation_id: "c_x", message_ids: [] }),
    ),
  );
  assert.equal(transport.status().failures > 0, true);
  await transport.stop();
});
