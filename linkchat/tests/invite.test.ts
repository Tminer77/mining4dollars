import assert from "node:assert/strict";
import { test } from "node:test";
import { ProtocolError } from "../src/protocol/errors.ts";
import {
  buildInviteLink,
  buildInviteUri,
  mintInvite,
  newInviteSecret,
  parseInviteLink,
  sanitiseHints,
  verifyInvite,
} from "../src/protocol/invite.ts";
import { randomKey } from "../src/crypto/primitives.ts";

const CONVERSATION = "c_0123456789ABCDEFGHJKMNPQRS";
const secret = newInviteSecret();

test("a minted invite verifies against its conversation", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  verifyInvite(invite, { conversationId: CONVERSATION, inviteSecret: secret });
});

test("an invite for one conversation does not work for another", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  assert.throws(
    () => verifyInvite(invite, { conversationId: "c_ZZZZZZZZZZZZZZZZZZZZZZZZZZ", inviteSecret: secret }),
    (error: ProtocolError) => error.code === "invite_invalid",
  );
});

test("an invite cannot be forged without the invite secret", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  assert.throws(
    () => verifyInvite(invite, { conversationId: CONVERSATION, inviteSecret: newInviteSecret() }),
    (error: ProtocolError) => error.code === "invite_invalid",
  );
});

test("extending an invite's expiry invalidates its mac", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret, ttlMs: 1000 });
  const extended = { ...invite, exp: invite.exp + 10 * 60 * 1000 };
  assert.throws(
    () => verifyInvite(extended, { conversationId: CONVERSATION, inviteSecret: secret }),
    (error: ProtocolError) => error.code === "invite_invalid",
  );
});

test("invites expire", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret, ttlMs: 50 });
  assert.throws(
    () => verifyInvite(invite, { conversationId: CONVERSATION, inviteSecret: secret, now: Date.now() + 1000 }),
    (error: ProtocolError) => error.code === "invite_expired",
  );
});

test("invites can be revoked by nonce", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  assert.throws(
    () =>
      verifyInvite(invite, {
        conversationId: CONVERSATION,
        inviteSecret: secret,
        revoked: new Set([invite.nonce]),
      }),
    (error: ProtocolError) => error.code === "invite_revoked",
  );
});

test("a link round-trips, and secrets live only in the fragment", () => {
  const conversationKey = randomKey();
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  const link = buildInviteLink({
    conversationId: CONVERSATION,
    conversationKey,
    invite,
    hints: [{ kind: "p2p", url: "ws://127.0.0.1:9000" }],
    invitedBy: "p_ABCDEFGHJKMNPQRS",
  });

  const [beforeFragment, fragment] = link.split("#");
  assert.ok(beforeFragment!.endsWith(`/join/${CONVERSATION}`));
  assert.equal(beforeFragment!.includes(conversationKey.toString("base64url")), false);
  assert.ok(fragment!.includes(conversationKey.toString("base64url")));
  assert.equal(beforeFragment!.includes(invite.mac), false);

  const parsed = parseInviteLink(link);
  assert.equal(parsed.conversationId, CONVERSATION);
  assert.deepEqual(parsed.conversationKey, conversationKey);
  assert.deepEqual(parsed.invite, invite);
  assert.equal(parsed.invitedBy, "p_ABCDEFGHJKMNPQRS");
  assert.deepEqual(parsed.hints, [{ kind: "p2p", url: "ws://127.0.0.1:9000" }]);
});

test("the link never carries the invite secret itself", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  const link = buildInviteLink({
    conversationId: CONVERSATION,
    conversationKey: randomKey(),
    invite,
    hints: [],
  });
  assert.equal(link.includes(secret.toString("base64url")), false);
});

test("the custom scheme carries the same payload", () => {
  const invite = mintInvite({ conversationId: CONVERSATION, inviteSecret: secret });
  const input = { conversationId: CONVERSATION, conversationKey: randomKey(), invite, hints: [] };
  const uri = buildInviteUri(input);
  assert.ok(uri.startsWith("linkchat://join/"));
  assert.equal(parseInviteLink(uri).conversationId, CONVERSATION);
});

test("malformed links are rejected with a reason", () => {
  const cases: [string, string][] = [
    ["https://linkchat.local/join/c_0123456789ABCDEFGHJKMNPQRS", "invite_invalid"],
    ["https://linkchat.local/somewhere#v=1", "invite_invalid"],
    ["https://linkchat.local/join/not-an-id#v=1&k=a&n=b&e=1&m=c", "invite_invalid"],
    ["https://linkchat.local/join/c_0123456789ABCDEFGHJKMNPQRS#v=99&k=a&n=b&e=1&m=c", "bad_protocol_version"],
    ["https://linkchat.local/join/c_0123456789ABCDEFGHJKMNPQRS#v=1&n=b&e=1&m=c", "invite_invalid"],
    ["https://linkchat.local/join/c_0123456789ABCDEFGHJKMNPQRS#v=1&k=AAAA&n=b&e=1&m=c", "bad_encoding"],
  ];
  for (const [link, code] of cases) {
    assert.throws(() => parseInviteLink(link), (error: ProtocolError) => error.code === code, link);
  }
});

test("hostile transport hints are dropped", () => {
  assert.deepEqual(
    sanitiseHints([
      { kind: "p2p", url: "javascript:alert(1)" },
      { kind: "p2p", url: "file:///etc/passwd" },
      { kind: "smtp", address: "not-an-address" },
      { kind: "p2p", url: "wss://relay.example:443" },
      { kind: "smtp", address: "bob@example.test" },
      "nonsense",
      null,
    ]),
    [
      { kind: "p2p", url: "wss://relay.example:443" },
      { kind: "smtp", address: "bob@example.test" },
    ],
  );
});
