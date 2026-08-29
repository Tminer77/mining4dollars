/**
 * Frame signing and replay rejection.
 *
 * Frames are signed by the *node* that sends them, which is not necessarily
 * the author of the records inside: when Bob relays Alice's message to Carol,
 * the frame is Bob's and the record is still Alice's. Both are verified
 * independently.
 */
import { canonicalBytes, fromBase64Url, toBase64Url, type JsonValue } from "../crypto/encoding.ts";
import { peerIdFromPublicKey } from "../crypto/ids.ts";
import { randomBytes, verify } from "../crypto/primitives.ts";
import type { Identity } from "../identity/identity.ts";
import { ProtocolError } from "./errors.ts";
import {
  MAX_FRAME_AGE_MS,
  MAX_FRAME_SKEW_MS,
  PROTOCOL_VERSION,
  type SignedFrame,
  type WireFrame,
} from "./types.ts";

function frameSigningBytes(frame: Omit<SignedFrame, "signature">): Buffer {
  return canonicalBytes(frame as unknown as JsonValue);
}

export function signFrame(identity: Identity, frame: WireFrame, now = Date.now()): SignedFrame {
  const unsigned: Omit<SignedFrame, "signature"> = {
    protocol: PROTOCOL_VERSION,
    sender_id: identity.peerId,
    sender_key: toBase64Url(identity.publicKey),
    sent_at: now,
    nonce: toBase64Url(randomBytes(16)),
    frame,
  };
  return { ...unsigned, signature: toBase64Url(identity.sign(frameSigningBytes(unsigned))) };
}

/**
 * Remembers frame nonces long enough to reject a replay, and forgets them
 * again so memory does not grow without bound. The window matches the age
 * limit frames are accepted within, so a frame can never outlive its entry.
 */
export class ReplayGuard {
  readonly #seen = new Map<string, number>();
  readonly #windowMs: number;

  constructor(windowMs = MAX_FRAME_AGE_MS) {
    this.#windowMs = windowMs;
  }

  /** True if this nonce is new; false if it has been seen inside the window. */
  admit(nonce: string, now = Date.now()): boolean {
    this.prune(now);
    if (this.#seen.has(nonce)) return false;
    this.#seen.set(nonce, now);
    return true;
  }

  prune(now = Date.now()): void {
    for (const [nonce, at] of this.#seen) {
      if (now - at > this.#windowMs) this.#seen.delete(nonce);
    }
  }

  get size(): number {
    return this.#seen.size;
  }
}

export type VerifyFrameOptions = {
  now?: number;
  replayGuard?: ReplayGuard;
  maxAgeMs?: number;
  maxSkewMs?: number;
};

export function verifyFrame(signed: SignedFrame, options: VerifyFrameOptions = {}): WireFrame {
  if (!signed || typeof signed !== "object") {
    throw new ProtocolError("bad_encoding", "frame is not an object");
  }
  if (signed.protocol !== PROTOCOL_VERSION) {
    throw new ProtocolError("bad_protocol_version", `unsupported protocol ${signed.protocol}`);
  }
  if (!signed.frame || typeof signed.frame !== "object" || typeof signed.frame.kind !== "string") {
    throw new ProtocolError("bad_encoding", "frame has no kind");
  }

  let publicKey: Buffer;
  let signature: Buffer;
  try {
    publicKey = fromBase64Url(signed.sender_key);
    signature = fromBase64Url(signed.signature);
  } catch {
    throw new ProtocolError("bad_encoding", "sender_key or signature is not base64url");
  }
  if (publicKey.length !== 32) {
    throw new ProtocolError("bad_encoding", "sender_key is not an Ed25519 public key");
  }
  if (peerIdFromPublicKey(publicKey) !== signed.sender_id) {
    throw new ProtocolError("bad_peer_id", "frame sender_id does not match sender_key");
  }

  const { signature: _omit, ...unsigned } = signed;
  if (!verify(frameSigningBytes(unsigned), signature, publicKey)) {
    throw new ProtocolError("bad_signature", "frame signature does not verify");
  }

  const now = options.now ?? Date.now();
  const maxAge = options.maxAgeMs ?? MAX_FRAME_AGE_MS;
  const maxSkew = options.maxSkewMs ?? MAX_FRAME_SKEW_MS;
  if (typeof signed.sent_at !== "number" || !Number.isFinite(signed.sent_at)) {
    throw new ProtocolError("bad_encoding", "sent_at is not a number");
  }
  if (signed.sent_at - now > maxSkew) {
    throw new ProtocolError("future_dated", "frame is dated in the future");
  }
  if (now - signed.sent_at > maxAge) {
    throw new ProtocolError("stale", "frame is older than the replay window");
  }
  if (options.replayGuard && !options.replayGuard.admit(signed.nonce, now)) {
    throw new ProtocolError("replayed", "frame nonce has already been seen");
  }

  return signed.frame;
}
