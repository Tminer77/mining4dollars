/**
 * The link *is* the invitation.
 *
 * Layout:
 *
 *   https://linkchat.local/join/c_01J…#v=1&k=…&n=…&e=…&m=…&h=…&c=…
 *   └────────── path: public ──────┘ └──── fragment: secret ─────┘
 *
 * Everything secret lives in the fragment, which browsers never send to a
 * server and which no HTTP log records. The path carries only the
 * conversation id, which is a random identifier and grants nothing on its own.
 *
 * Fragment parameters:
 *   v  link format version
 *   k  conversation key (base64url, 32 bytes) — the end-to-end encryption key
 *   n  invite nonce      — names this invite for revocation
 *   e  invite expiry     — epoch milliseconds
 *   m  invite MAC        — HMAC-SHA256 over (conversation, nonce, expiry)
 *   h  transport hints   — base64url JSON, how to reach a participant
 *   c  inviting peer id  — advisory, for display
 *
 * The MAC is keyed by the conversation's invite secret, which is NOT in the
 * link. A link holder can present the token they were given but cannot mint a
 * new one, so expiry and revocation actually bind. Once someone joins they
 * receive the invite secret and can invite others — that is intended.
 *
 * Possession of the link means the ability to read the conversation. That is
 * the product premise, not an oversight; see docs/LINKCHAT_PROTOCOL.md §
 * "What the link grants".
 */
import { canonicalBytes, fromBase64Url, toBase64Url } from "../crypto/encoding.ts";
import { constantTimeEquals, hmac, randomBytes } from "../crypto/primitives.ts";
import { ID_PATTERNS } from "../crypto/ids.ts";
import { ProtocolError } from "./errors.ts";
import type { InviteProof, TransportHint } from "./types.ts";

export const LINK_FORMAT_VERSION = "1";
export const DEFAULT_INVITE_TTL_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_LINK_ORIGIN = "https://linkchat.local";

export type ParsedInviteLink = {
  conversationId: string;
  conversationKey: Buffer;
  invite: InviteProof;
  hints: TransportHint[];
  invitedBy?: string;
};

function inviteBinding(conversationId: string, nonce: string, exp: number): Buffer {
  return canonicalBytes({ conversation_id: conversationId, nonce, exp });
}

export function newInviteSecret(): Buffer {
  return randomBytes(32);
}

export function mintInvite(input: {
  conversationId: string;
  inviteSecret: Buffer;
  ttlMs?: number;
  now?: number;
}): InviteProof {
  const now = input.now ?? Date.now();
  const exp = now + (input.ttlMs ?? DEFAULT_INVITE_TTL_MS);
  const nonce = toBase64Url(randomBytes(12));
  const mac = hmac(input.inviteSecret, inviteBinding(input.conversationId, nonce, exp));
  return { nonce, exp, mac: toBase64Url(mac) };
}

export type VerifyInviteOptions = {
  conversationId: string;
  inviteSecret: Buffer;
  now?: number;
  revoked?: ReadonlySet<string>;
};

/** Throws ProtocolError unless the invite is authentic, live, and unrevoked. */
export function verifyInvite(proof: InviteProof, options: VerifyInviteOptions): void {
  if (!proof || typeof proof.nonce !== "string" || typeof proof.mac !== "string") {
    throw new ProtocolError("invite_invalid", "invite is malformed");
  }
  if (!Number.isFinite(proof.exp)) {
    throw new ProtocolError("invite_invalid", "invite expiry is not a number");
  }
  let mac: Buffer;
  try {
    mac = fromBase64Url(proof.mac);
  } catch {
    throw new ProtocolError("invite_invalid", "invite mac is not base64url");
  }
  const expected = hmac(
    options.inviteSecret,
    inviteBinding(options.conversationId, proof.nonce, proof.exp),
  );
  if (!constantTimeEquals(mac, expected)) {
    throw new ProtocolError("invite_invalid", "invite mac does not verify");
  }
  // Revocation is checked before expiry so a revoked-and-expired invite
  // reports the more useful of the two reasons.
  if (options.revoked?.has(proof.nonce)) {
    throw new ProtocolError("invite_revoked", "invite has been revoked");
  }
  if ((options.now ?? Date.now()) > proof.exp) {
    throw new ProtocolError("invite_expired", "invite has expired");
  }
}

function encodeHints(hints: TransportHint[]): string {
  return toBase64Url(Buffer.from(JSON.stringify(hints), "utf8"));
}

/** Hints arrive from a stranger's link; only well-formed ones are kept. */
export function sanitiseHints(value: unknown): TransportHint[] {
  if (!Array.isArray(value)) return [];
  const out: TransportHint[] = [];
  for (const hint of value) {
    if (!hint || typeof hint !== "object") continue;
    const candidate = hint as { kind?: unknown; url?: unknown; address?: unknown };
    if (candidate.kind === "p2p" && typeof candidate.url === "string") {
      if (/^wss?:\/\/[^\s]+$/i.test(candidate.url)) out.push({ kind: "p2p", url: candidate.url });
    } else if (candidate.kind === "smtp" && typeof candidate.address === "string") {
      if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(candidate.address)) {
        out.push({ kind: "smtp", address: candidate.address });
      }
    }
  }
  return out;
}

export function buildInviteLink(input: {
  conversationId: string;
  conversationKey: Buffer;
  invite: InviteProof;
  hints: TransportHint[];
  invitedBy?: string;
  origin?: string;
}): string {
  const params = new URLSearchParams({
    v: LINK_FORMAT_VERSION,
    k: toBase64Url(input.conversationKey),
    n: input.invite.nonce,
    e: String(input.invite.exp),
    m: input.invite.mac,
    h: encodeHints(input.hints),
  });
  if (input.invitedBy) params.set("c", input.invitedBy);
  const origin = (input.origin ?? DEFAULT_LINK_ORIGIN).replace(/\/+$/, "");
  return `${origin}/join/${input.conversationId}#${params.toString()}`;
}

/** The custom-scheme form, for OS handlers. Same fragment, same meaning. */
export function buildInviteUri(input: Parameters<typeof buildInviteLink>[0]): string {
  const https = buildInviteLink({ ...input, origin: "https://linkchat.invalid" });
  return `linkchat://join/${https.slice(https.indexOf("/join/") + "/join/".length)}`;
}

export function parseInviteLink(link: string): ParsedInviteLink {
  const trimmed = link.trim();
  const hashIndex = trimmed.indexOf("#");
  if (hashIndex < 0) {
    throw new ProtocolError("invite_invalid", "link has no fragment; nothing to join with");
  }
  const beforeHash = trimmed.slice(0, hashIndex);
  const fragment = trimmed.slice(hashIndex + 1);

  const joinIndex = beforeHash.indexOf("/join/");
  if (joinIndex < 0) {
    throw new ProtocolError("invite_invalid", "link is not a /join/ link");
  }
  const conversationId = beforeHash.slice(joinIndex + "/join/".length).replace(/\/+$/, "");
  if (!ID_PATTERNS.conversation.test(conversationId)) {
    throw new ProtocolError("invite_invalid", `'${conversationId}' is not a conversation id`);
  }

  const params = new URLSearchParams(fragment);
  const version = params.get("v");
  if (version !== LINK_FORMAT_VERSION) {
    throw new ProtocolError("bad_protocol_version", `unsupported link version ${version ?? "?"}`);
  }

  const rawKey = params.get("k");
  const nonce = params.get("n");
  const exp = params.get("e");
  const mac = params.get("m");
  if (!rawKey || !nonce || !exp || !mac) {
    throw new ProtocolError("invite_invalid", "link is missing key or invite parameters");
  }

  let conversationKey: Buffer;
  try {
    conversationKey = fromBase64Url(rawKey);
  } catch {
    throw new ProtocolError("bad_encoding", "conversation key is not base64url");
  }
  if (conversationKey.length !== 32) {
    throw new ProtocolError("bad_encoding", "conversation key must be 32 bytes");
  }
  const expiry = Number(exp);
  if (!Number.isFinite(expiry)) {
    throw new ProtocolError("invite_invalid", "invite expiry is not a number");
  }

  let hints: TransportHint[] = [];
  const rawHints = params.get("h");
  if (rawHints) {
    try {
      hints = sanitiseHints(JSON.parse(fromBase64Url(rawHints).toString("utf8")));
    } catch {
      hints = [];
    }
  }

  const invitedBy = params.get("c") ?? undefined;
  return {
    conversationId,
    conversationKey,
    invite: { nonce, exp: expiry, mac },
    hints,
    ...(invitedBy && ID_PATTERNS.peer.test(invitedBy) ? { invitedBy } : {}),
  };
}
