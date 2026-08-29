/**
 * The LinkChat wire protocol, version `linkchat/1`.
 *
 * Two distinct things travel the network and it is worth keeping them apart:
 *
 *   Record — a durable, signed, end-to-end encrypted entry in a
 *            conversation's replicated log. Records are what participants
 *            actually converge on. They are transport-agnostic and survive
 *            being relayed by a third participant.
 *
 *   Frame  — a transient, signed envelope that carries records or control
 *            traffic (handshakes, sync requests, acks) between two nodes over
 *            one transport hop. Frames are never stored.
 *
 * Nothing in this file imports a transport, and no transport imports message
 * semantics. That is the whole point of the split.
 */
import type { SealedBox } from "../crypto/primitives.ts";

export const PROTOCOL_VERSION = "linkchat/1";
export type ProtocolVersion = typeof PROTOCOL_VERSION;

/** Media type used when a record set travels inside an SMTP message. */
export const LINKCHAT_MEDIA_TYPE = "application/linkchat+json";

// --- Records -------------------------------------------------------------

export type RecordType = "text" | "join" | "leave" | "profile" | "revoke_invite";

export type RecordHeader = {
  protocol: ProtocolVersion;
  type: RecordType;
  conversation_id: string;
  message_id: string;
  sender_id: string;
  /** base64url Ed25519 public key — lets a receiver verify without prior contact. */
  sender_key: string;
  /** ISO 8601 wall clock of the sender. Advisory: used for display and skew checks. */
  timestamp: string;
  /** Per-sender monotonic counter. Gaps are what sync detects. */
  seq: number;
  /** Lamport clock. Primary sort key; ties break on (sender_id, message_id). */
  lamport: number;
};

/**
 * `body` is AES-256-GCM ciphertext under the conversation key, with the
 * canonical header as associated data — so a header cannot be edited without
 * breaking decryption, and a body cannot be moved to another header.
 */
export type SignedRecord = {
  header: RecordHeader;
  body: SealedBox;
  /** base64url Ed25519 signature over canonical({header, body}). */
  signature: string;
};

// --- Record payloads (plaintext, inside the sealed body) ------------------

export type TextPayload = { text: string };

export type TransportHint =
  | { kind: "p2p"; url: string }
  | { kind: "smtp"; address: string };

export type InviteProof = {
  /** Unique per invite, and the handle a revocation names. */
  nonce: string;
  /** Expiry, epoch milliseconds. */
  exp: number;
  /** base64url HMAC-SHA256 over the invite binding, keyed by the invite secret. */
  mac: string;
};

export type JoinPayload = {
  display_name: string;
  transports: TransportHint[];
  /** Absent for the conversation creator, who admits themselves. */
  invite?: InviteProof;
  /**
   * Present only on the genesis record (the creator's own join). The invite
   * secret is deliberately *not* in the link: a link holder can present the
   * token they were given but cannot mint a fresh one, which is what makes
   * expiry and revocation enforceable. Participants get it once they are in,
   * so any participant can invite.
   */
  invite_secret?: string;
};

export type ProfilePayload = { display_name: string; transports: TransportHint[] };
export type LeavePayload = { reason?: string };
/**
 * Revoking an invite must not eject the people who already used it, and a
 * Lamport clock cannot tell "already joined" from "joining concurrently" -
 * a fresh joiner's clock always starts low. So the revoker states outright
 * whom it is grandfathering: the participants it had already admitted under
 * this nonce at the moment it revoked. Anyone else presenting that nonce,
 * including a concurrent joiner, is denied. Deny is the safe default for a
 * revocation.
 */
export type RevokeInvitePayload = { nonce: string; grandfathered: string[] };

export type RecordPayload =
  | TextPayload
  | JoinPayload
  | ProfilePayload
  | LeavePayload
  | RevokeInvitePayload;

// --- Frames --------------------------------------------------------------

export type TransportName = "p2p" | "smtp" | "local";

/** Opening frame of a direct connection; `challenge` is echoed back signed. */
export type HelloFrame = {
  kind: "hello";
  peer_id: string;
  public_key: string;
  display_name: string;
  conversation_id: string;
  challenge: string;
  transports: TransportHint[];
};

/** Proof of key possession: a signature over the *other* side's challenge. */
export type AuthFrame = {
  kind: "auth";
  peer_id: string;
  public_key: string;
  challenge: string;
  signature: string;
  accepted: boolean;
  reason?: string;
};

export type RecordsFrame = {
  kind: "records";
  conversation_id: string;
  records: SignedRecord[];
};

/** Anti-entropy: "here is the highest seq I hold per sender; send me the rest." */
export type SyncRequestFrame = {
  kind: "sync_request";
  conversation_id: string;
  watermarks: Record<string, number>;
};

export type SyncResponseFrame = {
  kind: "sync_response";
  conversation_id: string;
  records: SignedRecord[];
  /** False when the response was truncated and the peer should ask again. */
  complete: boolean;
};

export type AckFrame = {
  kind: "ack";
  conversation_id: string;
  message_ids: string[];
};

export type PresenceFrame = {
  kind: "presence";
  conversation_id: string;
  peer_id: string;
  online: boolean;
  transports: TransportHint[];
};

export type WireFrame =
  | HelloFrame
  | AuthFrame
  | RecordsFrame
  | SyncRequestFrame
  | SyncResponseFrame
  | AckFrame
  | PresenceFrame;

export type FrameKind = WireFrame["kind"];

/**
 * Every frame on every transport is signed by the sending node. Direct
 * connections could rely on the handshake alone, but SMTP has no connection
 * to bind to, so uniform frame signing is what makes a store-and-forward hop
 * as trustworthy as a socket.
 */
export type SignedFrame = {
  protocol: ProtocolVersion;
  sender_id: string;
  sender_key: string;
  /** Epoch milliseconds; bounded by MAX_FRAME_AGE_MS on receipt. */
  sent_at: number;
  /** base64url random value; remembered briefly to reject replays. */
  nonce: string;
  frame: WireFrame;
  signature: string;
};

/** Frames older than this are rejected outright (replay window). */
export const MAX_FRAME_AGE_MS = 7 * 24 * 60 * 60 * 1000;
/** Frames claiming to be from the future by more than this are rejected. */
export const MAX_FRAME_SKEW_MS = 5 * 60 * 1000;
/** Records dated further ahead than this are rejected; see docs/LINKCHAT_PROTOCOL.md. */
export const MAX_RECORD_SKEW_MS = 15 * 60 * 1000;
