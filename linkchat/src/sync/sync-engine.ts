/**
 * Anti-entropy synchronisation.
 *
 * Two peers reconcile by exchanging watermarks — the highest sequence number
 * each holds per author — rather than lists of message ids. That is
 * O(participants) on the wire regardless of how long the conversation is, and
 * it converges from any starting point: a peer that has been offline for a
 * week, a peer that has never seen the conversation, and a peer that missed
 * exactly one message all use the same exchange.
 *
 * Sync is what makes the transport question stop mattering. A message that
 * went out over SMTP and a message that never went out at all end up in the
 * same place: the next time the peers speak, the gap is detected and filled.
 */
import type { Conversation } from "../conversation/conversation.ts";
import type {
  SignedRecord,
  SyncRequestFrame,
  SyncResponseFrame,
} from "../protocol/types.ts";

/** Keeps one response inside a sensible frame size; the peer asks again. */
export const SYNC_BATCH_LIMIT = 200;

export function buildSyncRequest(conversation: Conversation): SyncRequestFrame {
  return {
    kind: "sync_request",
    conversation_id: conversation.id,
    watermarks: conversation.watermarks(),
  };
}

export function buildSyncResponse(
  conversation: Conversation,
  request: SyncRequestFrame,
  limit = SYNC_BATCH_LIMIT,
): SyncResponseFrame {
  const missing = conversation.missingFor(request.watermarks ?? {});
  const batch = missing.slice(0, limit);
  return {
    kind: "sync_response",
    conversation_id: conversation.id,
    records: batch,
    complete: batch.length === missing.length,
  };
}

export type IngestSummary = {
  added: SignedRecord[];
  duplicates: number;
  conflicts: number;
  rejected: { messageId: string; reason: string }[];
};

/**
 * Apply a batch of records. Every rejection is counted and reported: a record
 * that fails to verify is a security event, not something to swallow.
 */
export function ingestBatch(
  conversation: Conversation,
  records: SignedRecord[],
  now = Date.now(),
): IngestSummary {
  const summary: IngestSummary = { added: [], duplicates: 0, conflicts: 0, rejected: [] };
  for (const record of records) {
    try {
      const outcome = conversation.ingest(record, now);
      if (outcome.status === "added") summary.added.push(outcome.record);
      else if (outcome.status === "duplicate") summary.duplicates += 1;
      else {
        summary.conflicts += 1;
        summary.added.push(outcome.record);
      }
    } catch (error) {
      summary.rejected.push({
        messageId: record?.header?.message_id ?? "unknown",
        reason: (error as Error).message,
      });
    }
  }
  return summary;
}

export type SyncStats = {
  requestsSent: number;
  requestsServed: number;
  recordsSent: number;
  recordsReceived: number;
  recordsRejected: number;
  duplicatesSuppressed: number;
  lastSyncAt: number | null;
  syncingWith: string[];
};

export class SyncTracker {
  #requestsSent = 0;
  #requestsServed = 0;
  #recordsSent = 0;
  #recordsReceived = 0;
  #recordsRejected = 0;
  #duplicates = 0;
  #lastSyncAt: number | null = null;
  readonly #inFlight = new Map<string, number>();
  readonly #inFlightTimeoutMs: number;

  constructor(inFlightTimeoutMs = 15_000) {
    this.#inFlightTimeoutMs = inFlightTimeoutMs;
  }

  requestSent(peerId: string, now = Date.now()): void {
    this.#requestsSent += 1;
    this.#inFlight.set(peerId, now);
  }

  /**
   * A request that was never answered must not leave the UI claiming to be
   * synchronizing forever - over SMTP an answer can legitimately never come.
   */
  #live(now = Date.now()): string[] {
    const out: string[] = [];
    for (const [peerId, at] of this.#inFlight) {
      if (now - at > this.#inFlightTimeoutMs) this.#inFlight.delete(peerId);
      else out.push(peerId);
    }
    return out;
  }

  requestServed(count: number): void {
    this.#requestsServed += 1;
    this.#recordsSent += count;
  }

  responseReceived(peerId: string, summary: IngestSummary, now = Date.now()): void {
    this.#inFlight.delete(peerId);
    this.#recordsReceived += summary.added.length;
    this.#recordsRejected += summary.rejected.length;
    this.#duplicates += summary.duplicates;
    this.#lastSyncAt = now;
  }

  get syncing(): boolean {
    return this.#live().length > 0;
  }

  stats(): SyncStats {
    return {
      requestsSent: this.#requestsSent,
      requestsServed: this.#requestsServed,
      recordsSent: this.#recordsSent,
      recordsReceived: this.#recordsReceived,
      recordsRejected: this.#recordsRejected,
      duplicatesSuppressed: this.#duplicates,
      lastSyncAt: this.#lastSyncAt,
      syncingWith: this.#live(),
    };
  }
}
