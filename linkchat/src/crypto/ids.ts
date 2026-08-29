/**
 * Identifier minting.
 *
 * Conversation and message ids are ULID-shaped: a 48-bit millisecond
 * timestamp followed by 80 bits of randomness, Crockford base32. That gives
 * global uniqueness without coordination *and* lexicographic sortability,
 * which the message log leans on as a stable tiebreaker.
 */
import { randomBytes, sha256 } from "./primitives.ts";
import { toBase32 } from "./encoding.ts";

let lastMillis = 0;
let lastRandom = randomBytes(10);

function ulidBytes(now: number): Buffer {
  const time = Buffer.alloc(6);
  time.writeUIntBE(now, 0, 6);
  if (now === lastMillis) {
    // Same millisecond: increment the previous randomness so ids minted in a
    // tight loop still sort in creation order.
    const next = Buffer.from(lastRandom);
    for (let i = next.length - 1; i >= 0; i -= 1) {
      const byte = next[i] ?? 0;
      if (byte === 0xff) {
        next[i] = 0;
        continue;
      }
      next[i] = byte + 1;
      break;
    }
    lastRandom = next;
  } else {
    lastMillis = now;
    lastRandom = randomBytes(10);
  }
  return Buffer.concat([time, lastRandom]);
}

export function ulid(now = Date.now()): string {
  return toBase32(ulidBytes(now));
}

export function newConversationId(now = Date.now()): string {
  return `c_${ulid(now)}`;
}

export function newMessageId(now = Date.now()): string {
  return `m_${ulid(now)}`;
}

/**
 * A peer id is a fingerprint of the public key, so it is self-authenticating:
 * given a claimed id and a public key, anyone can check that they match. It is
 * not an email address and it is not assigned by a server.
 */
export function peerIdFromPublicKey(publicKey: Buffer): string {
  return `p_${toBase32(sha256(publicKey).subarray(0, 10))}`;
}

export const ID_PATTERNS = {
  peer: /^p_[0-9A-HJKMNP-TV-Z]{16}$/,
  conversation: /^c_[0-9A-HJKMNP-TV-Z]{26}$/,
  message: /^m_[0-9A-HJKMNP-TV-Z]{26}$/,
} as const;
