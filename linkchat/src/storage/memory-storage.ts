/** In-memory storage. Used by tests and by `--ephemeral` nodes. */
import type { SignedRecord } from "../protocol/types.ts";
import type { ConversationStorage, StoredConversationMeta } from "./types.ts";

export class MemoryStorage implements ConversationStorage {
  readonly #meta = new Map<string, StoredConversationMeta>();
  readonly #records = new Map<string, SignedRecord[]>();

  listMeta(): StoredConversationMeta[] {
    return [...this.#meta.values()].map((meta) => structuredClone(meta));
  }

  loadMeta(conversationId: string): StoredConversationMeta | null {
    const meta = this.#meta.get(conversationId);
    return meta ? structuredClone(meta) : null;
  }

  saveMeta(meta: StoredConversationMeta): void {
    this.#meta.set(meta.conversation_id, structuredClone(meta));
  }

  readRecords(conversationId: string): SignedRecord[] {
    return [...(this.#records.get(conversationId) ?? [])];
  }

  appendRecords(conversationId: string, records: SignedRecord[]): void {
    const existing = this.#records.get(conversationId) ?? [];
    existing.push(...records);
    this.#records.set(conversationId, existing);
  }
}
