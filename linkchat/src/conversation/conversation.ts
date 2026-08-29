/**
 * A conversation: the log, its key material, and the operations that append
 * to it. This is the layer the UI and the node talk to; it knows nothing
 * about sockets or SMTP.
 */
import { fromBase64Url, toBase64Url } from "../crypto/encoding.ts";
import { newConversationId } from "../crypto/ids.ts";
import { randomKey } from "../crypto/primitives.ts";
import type { Identity } from "../identity/identity.ts";
import { MessageLog, type AppendOutcome } from "../messages/log.ts";
import { ProtocolError } from "../protocol/errors.ts";
import {
  DEFAULT_INVITE_TTL_MS,
  DEFAULT_LINK_ORIGIN,
  buildInviteLink,
  buildInviteUri,
  mintInvite,
  newInviteSecret,
  parseInviteLink,
  verifyInvite,
} from "../protocol/invite.ts";
import { buildRecord, openRecord, verifyRecord } from "../protocol/records.ts";
import type {
  InviteProof,
  JoinPayload,
  RecordPayload,
  RecordType,
  SignedRecord,
  TransportHint,
} from "../protocol/types.ts";
import type { ConversationStorage, StoredConversationMeta } from "../storage/types.ts";
import { computeConversationState, type ConversationState } from "./state.ts";

export type ConversationDeps = {
  identity: Identity;
  storage: ConversationStorage;
  linkOrigin?: string;
};

export class Conversation {
  readonly id: string;
  readonly log: MessageLog;
  readonly #identity: Identity;
  readonly #storage: ConversationStorage;
  readonly #linkOrigin: string;
  #meta: StoredConversationMeta;

  private constructor(meta: StoredConversationMeta, deps: ConversationDeps, records: SignedRecord[]) {
    this.id = meta.conversation_id;
    this.#meta = meta;
    this.#identity = deps.identity;
    this.#storage = deps.storage;
    this.#linkOrigin = deps.linkOrigin ?? DEFAULT_LINK_ORIGIN;
    this.log = new MessageLog(meta.conversation_id, records);
  }

  // --- construction ------------------------------------------------------

  /** Create a new conversation and write its genesis join record. */
  static create(
    deps: ConversationDeps,
    options: { title?: string; hints?: TransportHint[]; now?: number },
  ): Conversation {
    const now = options.now ?? Date.now();
    const conversationId = newConversationId(now);
    const meta: StoredConversationMeta = {
      conversation_id: conversationId,
      created_at: new Date(now).toISOString(),
      creator_id: deps.identity.peerId,
      title: options.title ?? "Conversation",
      conversation_key: toBase64Url(randomKey()),
      invite_secret: toBase64Url(newInviteSecret()),
      self_hints: options.hints ?? [],
      revoked_invites: [],
    };
    deps.storage.saveMeta(meta);
    const conversation = new Conversation(meta, deps, []);
    // The genesis record: a join carrying the invite secret, which is how
    // every later participant learns how to validate and mint invites.
    conversation.#appendLocal("join", {
      display_name: deps.identity.displayName,
      transports: meta.self_hints,
      invite_secret: meta.invite_secret ?? undefined,
    } satisfies JoinPayload, now);
    return conversation;
  }

  /**
   * Prepare local state for joining via an invite link, and write our own
   * join record. Nothing is admitted yet: the join has to reach a participant
   * that can validate the invite, which is what the node's connect flow does.
   */
  static joinFromLink(
    deps: ConversationDeps,
    options: { link: string; hints?: TransportHint[]; now?: number },
  ): { conversation: Conversation; invite: InviteProof; hints: TransportHint[] } {
    const parsed = parseInviteLink(options.link);
    const now = options.now ?? Date.now();
    if (parsed.invite.exp < now) {
      throw new ProtocolError("invite_expired", "this invite link has expired");
    }

    const existing = deps.storage.loadMeta(parsed.conversationId);
    if (existing) {
      const conversation = Conversation.open(deps, parsed.conversationId);
      return { conversation, invite: parsed.invite, hints: parsed.hints };
    }

    const meta: StoredConversationMeta = {
      conversation_id: parsed.conversationId,
      created_at: new Date(now).toISOString(),
      creator_id: parsed.invitedBy ?? "unknown",
      title: "Conversation",
      conversation_key: toBase64Url(parsed.conversationKey),
      // Learned from the genesis record once we sync; not in the link.
      invite_secret: null,
      self_hints: options.hints ?? [],
      revoked_invites: [],
    };
    deps.storage.saveMeta(meta);
    const conversation = new Conversation(meta, deps, []);
    conversation.#appendLocal("join", {
      display_name: deps.identity.displayName,
      transports: meta.self_hints,
      invite: parsed.invite,
    } satisfies JoinPayload, now);
    return { conversation, invite: parsed.invite, hints: parsed.hints };
  }

  static open(deps: ConversationDeps, conversationId: string): Conversation {
    const meta = deps.storage.loadMeta(conversationId);
    if (!meta) throw new Error(`conversation ${conversationId} is not on this device`);
    return new Conversation(meta, deps, deps.storage.readRecords(conversationId));
  }

  static openAll(deps: ConversationDeps): Conversation[] {
    return deps.storage
      .listMeta()
      .map((meta) => new Conversation(meta, deps, deps.storage.readRecords(meta.conversation_id)));
  }

  // --- key material ------------------------------------------------------

  get meta(): StoredConversationMeta {
    return { ...this.#meta };
  }

  get title(): string {
    return this.#meta.title;
  }

  get conversationKey(): Buffer {
    return fromBase64Url(this.#meta.conversation_key);
  }

  /** Present once we hold the genesis record; null while still syncing. */
  get inviteSecret(): Buffer | null {
    if (this.#meta.invite_secret) return fromBase64Url(this.#meta.invite_secret);
    const fromLog = this.state().inviteSecret;
    if (!fromLog) return null;
    // Cache it so later invites do not have to recompute the whole state.
    this.#meta = { ...this.#meta, invite_secret: fromLog };
    this.#storage.saveMeta(this.#meta);
    return fromBase64Url(fromLog);
  }

  get selfHints(): TransportHint[] {
    return [...this.#meta.self_hints];
  }

  // --- appending ---------------------------------------------------------

  #appendLocal(type: RecordType, payload: RecordPayload, now = Date.now()): SignedRecord {
    const record = buildRecord({
      identity: this.#identity,
      conversationId: this.id,
      conversationKey: this.conversationKey,
      type,
      payload,
      seq: this.log.nextSeq(this.#identity.peerId),
      lamport: this.log.nextLamport(),
      now,
    });
    this.log.append(record);
    this.#storage.appendRecords(this.id, [record]);
    return record;
  }

  post(text: string, now = Date.now()): SignedRecord {
    if (typeof text !== "string" || text.trim().length === 0) {
      throw new Error("cannot post an empty message");
    }
    return this.#appendLocal("text", { text }, now);
  }

  leave(reason?: string): SignedRecord {
    return this.#appendLocal("leave", reason ? { reason } : {});
  }

  /** Re-advertise our transports, e.g. after the P2P listener gets a port. */
  updateHints(hints: TransportHint[]): SignedRecord | null {
    const before = JSON.stringify(this.#meta.self_hints);
    if (before === JSON.stringify(hints)) return null;
    this.#meta = { ...this.#meta, self_hints: hints };
    this.#storage.saveMeta(this.#meta);
    return this.#appendLocal("profile", {
      display_name: this.#identity.displayName,
      transports: hints,
    });
  }

  /**
   * Accept a record that arrived over some transport.
   *
   * Two gates, in order: the signature must verify (authenticity), and the
   * body must decrypt under the conversation key (proof the sender holds the
   * key, which keeps strangers from appending to the log). Whether the sender
   * was properly *invited* is a separate question, answered by the state
   * computation, because it depends on records that may not have arrived yet.
   */
  ingest(record: SignedRecord, now = Date.now()): AppendOutcome {
    verifyRecord(record, { conversationId: this.id, now });
    openRecord(record, this.conversationKey);
    const outcome = this.log.append(record);
    if (outcome.status !== "duplicate") {
      this.#storage.appendRecords(this.id, [record]);
    }
    return outcome;
  }

  // --- invitations -------------------------------------------------------

  createInvite(options: { ttlMs?: number; hints?: TransportHint[]; now?: number } = {}): {
    link: string;
    uri: string;
    invite: InviteProof;
  } {
    const inviteSecret = this.inviteSecret;
    if (!inviteSecret) {
      throw new ProtocolError(
        "invite_invalid",
        "cannot mint an invite before syncing the conversation's genesis record",
      );
    }
    const invite = mintInvite({
      conversationId: this.id,
      inviteSecret,
      ttlMs: options.ttlMs ?? DEFAULT_INVITE_TTL_MS,
      now: options.now,
    });
    const linkInput = {
      conversationId: this.id,
      conversationKey: this.conversationKey,
      invite,
      hints: options.hints ?? this.reachableHints(),
      invitedBy: this.#identity.peerId,
      origin: this.#linkOrigin,
    };
    return { link: buildInviteLink(linkInput), uri: buildInviteUri(linkInput), invite };
  }

  /**
   * Hints to publish in a link: ours, plus every other participant's. A
   * joiner that cannot reach us may still reach someone else in the
   * conversation, and any participant can admit them.
   */
  reachableHints(): TransportHint[] {
    const seen = new Set<string>();
    const out: TransportHint[] = [];
    for (const hint of [...this.#meta.self_hints, ...this.state().participants.flatMap((p) => p.transports)]) {
      const key = JSON.stringify(hint);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(hint);
    }
    return out;
  }

  /**
   * Revoke an invite. Participants already admitted under that nonce are
   * named in the record so they are not ejected; anyone else presenting it
   * from now on is refused, on every node, forever.
   */
  revokeInvite(nonce: string): SignedRecord {
    const revoked = new Set(this.#meta.revoked_invites);
    revoked.add(nonce);
    this.#meta = { ...this.#meta, revoked_invites: [...revoked] };
    this.#storage.saveMeta(this.#meta);
    const grandfathered = this.state()
      .participants.filter((participant) => participant.admitted)
      .map((participant) => participant.peerId);
    return this.#appendLocal("revoke_invite", { nonce, grandfathered });
  }

  /**
   * Live admission check, run by whichever participant is handling a join
   * handshake. Unlike the log-level check this uses the local clock, so a
   * backdated join record cannot resurrect an expired invite on a peer that
   * is actually online.
   */
  admits(invite: InviteProof, now = Date.now()): { ok: true } | { ok: false; reason: string } {
    const inviteSecret = this.inviteSecret;
    if (!inviteSecret) return { ok: false, reason: "invite secret not yet synced" };
    try {
      verifyInvite(invite, {
        conversationId: this.id,
        inviteSecret,
        now,
        revoked: new Set([...this.#meta.revoked_invites, ...this.state().revokedInvites]),
      });
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: error instanceof ProtocolError ? error.code : "invite_invalid" };
    }
  }

  // --- views -------------------------------------------------------------

  state(): ConversationState {
    return computeConversationState(this.id, this.log.all(), {
      selfPeerId: this.#identity.peerId,
      conversationKey: this.conversationKey,
      inviteSecret: this.#meta.invite_secret ? fromBase64Url(this.#meta.invite_secret) : null,
    });
  }

  records(): SignedRecord[] {
    return this.log.all();
  }

  watermarks(): Record<string, number> {
    return this.log.watermarks();
  }

  missingFor(theirs: Record<string, number>): SignedRecord[] {
    return this.log.missingFor(theirs);
  }
}
