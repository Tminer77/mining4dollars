/**
 * The conversation log.
 *
 * A conversation is a set of signed records, not a stream of packets. Every
 * node holds the whole set; delivery order does not matter; the same record
 * arriving twice is a no-op. That is what makes "Bob was offline for an hour"
 * and "Bob's message came in over SMTP after Carol's came in over P2P" the
 * same problem, solved once.
 *
 * Ordering is a Lamport clock with a deterministic tiebreak, so every node
 * that holds the same set displays it in the same order. It is not a
 * wall-clock order: a message written while offline appears where it happened
 * causally, not where its timestamp claims.
 */
import { compareRecords } from "../protocol/records.ts";
import type { SignedRecord } from "../protocol/types.ts";

export type AppendOutcome =
  | { status: "added"; record: SignedRecord }
  | { status: "duplicate"; record: SignedRecord }
  /** Same sender and seq, different message: the sender equivocated or forked. */
  | { status: "conflict"; record: SignedRecord; existing: SignedRecord };

export class MessageLog {
  readonly conversationId: string;
  readonly #byId = new Map<string, SignedRecord>();
  readonly #bySenderSeq = new Map<string, SignedRecord>();
  readonly #watermarks = new Map<string, number>();
  #lamport = 0;
  #conflicts = 0;

  constructor(conversationId: string, initial: SignedRecord[] = []) {
    this.conversationId = conversationId;
    for (const record of initial) this.append(record);
  }

  /**
   * Add a record that has already passed `verifyRecord`. The log does not
   * verify signatures itself — that belongs at the boundary, and doing it here
   * too would mean re-verifying the whole log on every restart.
   */
  append(record: SignedRecord): AppendOutcome {
    const { message_id, sender_id, seq, lamport } = record.header;
    const existingById = this.#byId.get(message_id);
    if (existingById) return { status: "duplicate", record: existingById };

    const seqKey = `${sender_id}/${seq}`;
    const existingBySeq = this.#bySenderSeq.get(seqKey);
    if (existingBySeq) {
      // Keep both: the log is append-only and a fork is evidence, not
      // something to silently discard. Surfaced through `conflicts` so the
      // diagnostics panel can show it.
      this.#conflicts += 1;
      this.#byId.set(message_id, record);
      this.#observeLamport(lamport);
      return { status: "conflict", record, existing: existingBySeq };
    }

    this.#byId.set(message_id, record);
    this.#bySenderSeq.set(seqKey, record);
    const previous = this.#watermarks.get(sender_id) ?? 0;
    if (seq > previous) this.#watermarks.set(sender_id, seq);
    this.#observeLamport(lamport);
    return { status: "added", record };
  }

  #observeLamport(seen: number): void {
    if (seen > this.#lamport) this.#lamport = seen;
  }

  has(messageId: string): boolean {
    return this.#byId.has(messageId);
  }

  get(messageId: string): SignedRecord | undefined {
    return this.#byId.get(messageId);
  }

  get size(): number {
    return this.#byId.size;
  }

  get conflicts(): number {
    return this.#conflicts;
  }

  /** Every record, in the order every other node will also display them. */
  all(): SignedRecord[] {
    return [...this.#byId.values()].sort(compareRecords);
  }

  byType(type: SignedRecord["header"]["type"]): SignedRecord[] {
    return this.all().filter((record) => record.header.type === type);
  }

  /** Next sequence number for this node's own records. */
  nextSeq(peerId: string): number {
    return (this.#watermarks.get(peerId) ?? 0) + 1;
  }

  /** Lamport clock value to stamp on a new record. */
  nextLamport(): number {
    return this.#lamport + 1;
  }

  /**
   * The summary a peer needs to work out what we are missing: the highest
   * contiguous-or-not sequence number seen per sender. Sending this instead of
   * a list of ids keeps sync O(participants) rather than O(messages).
   */
  watermarks(): Record<string, number> {
    return Object.fromEntries(this.#watermarks);
  }

  /**
   * Records this log holds that a peer with these watermarks does not.
   *
   * A sender absent from `theirs` is treated as watermark 0, so a brand-new
   * participant asking with `{}` receives the entire history.
   */
  missingFor(theirs: Record<string, number>): SignedRecord[] {
    const out: SignedRecord[] = [];
    for (const record of this.#byId.values()) {
      const theirWatermark = theirs[record.header.sender_id] ?? 0;
      if (record.header.seq > theirWatermark) out.push(record);
    }
    return out.sort(compareRecords);
  }

  /** Senders where the peer is ahead of us — what we should ask them for. */
  behind(theirs: Record<string, number>): string[] {
    const out: string[] = [];
    for (const [senderId, theirSeq] of Object.entries(theirs)) {
      if (theirSeq > (this.#watermarks.get(senderId) ?? 0)) out.push(senderId);
    }
    return out;
  }
}
