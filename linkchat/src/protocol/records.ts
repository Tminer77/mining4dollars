/**
 * Building and checking records — the durable unit of a conversation.
 *
 * A record is signed by its author and encrypted to the conversation key.
 * Verification is deliberately split in two:
 *
 *   verifyRecord — authenticity and freshness. Needs no conversation key, so
 *                  a relaying participant can validate what it forwards.
 *   openRecord   — decryption. Needs the conversation key, so only members
 *                  read content. A relay that is missing the key can still
 *                  carry traffic it cannot read.
 */
import { canonicalBytes, fromBase64Url, toBase64Url, type JsonValue } from "../crypto/encoding.ts";
import { newMessageId, peerIdFromPublicKey } from "../crypto/ids.ts";
import { open, seal, verify } from "../crypto/primitives.ts";
import type { Identity } from "../identity/identity.ts";
import { ProtocolError } from "./errors.ts";
import {
  MAX_RECORD_SKEW_MS,
  PROTOCOL_VERSION,
  type RecordHeader,
  type RecordPayload,
  type RecordType,
  type SignedRecord,
} from "./types.ts";

export type BuildRecordInput = {
  identity: Identity;
  conversationId: string;
  conversationKey: Buffer;
  type: RecordType;
  payload: RecordPayload;
  seq: number;
  lamport: number;
  now?: number;
  messageId?: string;
};

function headerAad(header: RecordHeader): Buffer {
  return canonicalBytes(header as unknown as JsonValue);
}

function signingBytes(header: RecordHeader, body: SignedRecord["body"]): Buffer {
  return canonicalBytes({ header, body } as unknown as JsonValue);
}

export function buildRecord(input: BuildRecordInput): SignedRecord {
  const now = input.now ?? Date.now();
  const header: RecordHeader = {
    protocol: PROTOCOL_VERSION,
    type: input.type,
    conversation_id: input.conversationId,
    message_id: input.messageId ?? newMessageId(now),
    sender_id: input.identity.peerId,
    sender_key: toBase64Url(input.identity.publicKey),
    timestamp: new Date(now).toISOString(),
    seq: input.seq,
    lamport: input.lamport,
  };
  const body = seal(
    Buffer.from(JSON.stringify(input.payload), "utf8"),
    input.conversationKey,
    headerAad(header),
  );
  const signature = toBase64Url(input.identity.sign(signingBytes(header, body)));
  return { header, body, signature };
}

export type VerifyRecordOptions = {
  conversationId?: string;
  now?: number;
  maxSkewMs?: number;
};

/**
 * Throws ProtocolError on anything that does not check out. Never returns a
 * boolean — callers have repeatedly proved they will ignore one.
 */
export function verifyRecord(record: SignedRecord, options: VerifyRecordOptions = {}): void {
  const { header } = record;
  if (!header || typeof header !== "object") {
    throw new ProtocolError("bad_encoding", "record has no header");
  }
  if (header.protocol !== PROTOCOL_VERSION) {
    throw new ProtocolError("bad_protocol_version", `unsupported protocol ${header.protocol}`);
  }
  if (options.conversationId && header.conversation_id !== options.conversationId) {
    throw new ProtocolError(
      "wrong_conversation",
      `record belongs to ${header.conversation_id}, not ${options.conversationId}`,
    );
  }
  if (!Number.isInteger(header.seq) || header.seq < 0) {
    throw new ProtocolError("bad_encoding", "seq must be a non-negative integer");
  }
  if (!Number.isInteger(header.lamport) || header.lamport < 0) {
    throw new ProtocolError("bad_encoding", "lamport must be a non-negative integer");
  }

  let publicKey: Buffer;
  let signature: Buffer;
  try {
    publicKey = fromBase64Url(header.sender_key);
    signature = fromBase64Url(record.signature);
  } catch {
    throw new ProtocolError("bad_encoding", "sender_key or signature is not base64url");
  }
  if (publicKey.length !== 32) {
    throw new ProtocolError("bad_encoding", "sender_key is not an Ed25519 public key");
  }
  // The peer id is a hash of the key, so this is what stops one peer from
  // signing records under another peer's id.
  if (peerIdFromPublicKey(publicKey) !== header.sender_id) {
    throw new ProtocolError("bad_peer_id", "sender_id does not match sender_key");
  }
  if (!verify(signingBytes(header, record.body), signature, publicKey)) {
    throw new ProtocolError("bad_signature", "record signature does not verify");
  }

  const timestamp = Date.parse(header.timestamp);
  if (Number.isNaN(timestamp)) {
    throw new ProtocolError("bad_encoding", "timestamp is not a valid ISO 8601 instant");
  }
  const now = options.now ?? Date.now();
  const skew = options.maxSkewMs ?? MAX_RECORD_SKEW_MS;
  if (timestamp - now > skew) {
    throw new ProtocolError("future_dated", "record timestamp is too far in the future");
  }
  // Deliberately no lower bound: store-and-forward means a legitimate record
  // can arrive days late. Replay is prevented by message_id dedupe and by the
  // per-sender sequence, not by a freshness window.
}

export function openRecord(record: SignedRecord, conversationKey: Buffer): RecordPayload {
  const plaintext = open(record.body, conversationKey, headerAad(record.header));
  if (!plaintext) {
    throw new ProtocolError("undecryptable", "record body did not authenticate under this key");
  }
  try {
    return JSON.parse(plaintext.toString("utf8")) as RecordPayload;
  } catch {
    throw new ProtocolError("bad_encoding", "record payload is not JSON");
  }
}

/** Total order over a conversation log: Lamport clock, then a stable tiebreak. */
export function compareRecords(a: SignedRecord, b: SignedRecord): number {
  if (a.header.lamport !== b.header.lamport) return a.header.lamport - b.header.lamport;
  if (a.header.sender_id !== b.header.sender_id) {
    return a.header.sender_id < b.header.sender_id ? -1 : 1;
  }
  if (a.header.message_id === b.header.message_id) return 0;
  return a.header.message_id < b.header.message_id ? -1 : 1;
}
