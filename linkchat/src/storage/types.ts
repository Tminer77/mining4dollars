/**
 * The storage port. Everything above it works against this interface, so the
 * file-backed store below can be swapped for SQLite later without the
 * conversation, sync, or transport layers noticing.
 */
import type { SignedRecord, TransportHint } from "../protocol/types.ts";

export type StoredConversationMeta = {
  conversation_id: string;
  created_at: string;
  creator_id: string;
  title: string;
  /** base64url conversation key (AES-256-GCM, end-to-end). */
  conversation_key: string;
  /** base64url invite secret. Only held by participants, never in a link. */
  invite_secret: string | null;
  /** Where this node advertises itself for this conversation. */
  self_hints: TransportHint[];
  /** Invite nonces this node refuses to admit. Replicated as revoke records. */
  revoked_invites: string[];
};

export interface ConversationStorage {
  listMeta(): StoredConversationMeta[];
  loadMeta(conversationId: string): StoredConversationMeta | null;
  saveMeta(meta: StoredConversationMeta): void;
  readRecords(conversationId: string): SignedRecord[];
  appendRecords(conversationId: string, records: SignedRecord[]): void;
}
