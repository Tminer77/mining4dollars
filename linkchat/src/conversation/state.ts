/**
 * Conversation state is a pure function of the record log.
 *
 * Nothing here mutates or performs I/O: give it the same records and it
 * produces the same participants and the same message list, in the same
 * order, on every node. That is what makes "did Carol's join reach me before
 * or after Alice's message?" stop mattering.
 */
import { fromBase64Url } from "../crypto/encoding.ts";
import { openRecord } from "../protocol/records.ts";
import { verifyInvite } from "../protocol/invite.ts";
import { ProtocolError } from "../protocol/errors.ts";
import type {
  JoinPayload,
  LeavePayload,
  ProfilePayload,
  RevokeInvitePayload,
  SignedRecord,
  TextPayload,
  TransportHint,
} from "../protocol/types.ts";

export type Participant = {
  peerId: string;
  displayName: string;
  /** base64url Ed25519 public key, taken from the signed join record. */
  publicKey: string;
  transports: TransportHint[];
  joinedAt: string;
  isCreator: boolean;
  isSelf: boolean;
  hasLeft: boolean;
  /** False when the join could not be justified by a valid invite. */
  admitted: boolean;
  admissionError?: string;
};

export type ChatMessage = {
  messageId: string;
  conversationId: string;
  senderId: string;
  senderName: string;
  text: string;
  timestamp: string;
  seq: number;
  lamport: number;
  mine: boolean;
  /** False when the sender never presented a valid invite; shown greyed out. */
  fromAdmittedPeer: boolean;
};

export type ConversationState = {
  conversationId: string;
  creatorId: string | null;
  /** base64url invite secret recovered from the genesis record, if present. */
  inviteSecret: string | null;
  participants: Participant[];
  messages: ChatMessage[];
  revokedInvites: string[];
  /** Records that failed to decrypt or parse - surfaced, never silently dropped. */
  undecryptable: number;
};

type DecodedRecord = { record: SignedRecord; payload: unknown };

export type ComputeOptions = {
  selfPeerId: string;
  conversationKey: Buffer;
  /** Falls back to the secret carried by the genesis record. */
  inviteSecret?: Buffer | null;
};

/** Records must already be in total order (MessageLog.all()). */
export function computeConversationState(
  conversationId: string,
  ordered: SignedRecord[],
  options: ComputeOptions,
): ConversationState {
  const decoded: DecodedRecord[] = [];
  let undecryptable = 0;
  for (const record of ordered) {
    try {
      decoded.push({ record, payload: openRecord(record, options.conversationKey) });
    } catch {
      undecryptable += 1;
    }
  }

  // The genesis record is the first join, in total order, that carries the
  // invite secret. Its sender is the creator. Every node picks the same one.
  let creatorId: string | null = null;
  let inviteSecretB64: string | null = null;
  for (const { record, payload } of decoded) {
    if (record.header.type !== "join") continue;
    const join = payload as JoinPayload;
    if (typeof join.invite_secret === "string") {
      creatorId = record.header.sender_id;
      inviteSecretB64 = join.invite_secret;
      break;
    }
  }

  let inviteSecret: Buffer | null = options.inviteSecret ?? null;
  if (!inviteSecret && inviteSecretB64) {
    try {
      inviteSecret = fromBase64Url(inviteSecretB64);
    } catch {
      inviteSecret = null;
    }
  }

  // Revocations, keyed by nonce, each carrying the participants it spares.
  // First revocation of a nonce wins, so every node reaches the same verdict.
  const revoked = new Map<string, Set<string>>();
  for (const { record, payload } of decoded) {
    if (record.header.type !== "revoke_invite") continue;
    const revocation = payload as RevokeInvitePayload;
    if (typeof revocation.nonce !== "string") continue;
    if (revoked.has(revocation.nonce)) continue;
    revoked.set(
      revocation.nonce,
      new Set(Array.isArray(revocation.grandfathered) ? revocation.grandfathered : []),
    );
  }

  const participants = new Map<string, Participant>();
  for (const { record, payload } of decoded) {
    const senderId = record.header.sender_id;
    if (record.header.type === "join") {
      if (participants.has(senderId)) continue; // first join wins
      const join = payload as JoinPayload;
      const isCreator = senderId === creatorId;
      let admitted = isCreator;
      let admissionError: string | undefined;
      if (!isCreator) {
        const outcome = checkAdmission(record, join, {
          conversationId,
          inviteSecret,
          revoked,
        });
        admitted = outcome.admitted;
        admissionError = outcome.error;
      }
      participants.set(senderId, {
        peerId: senderId,
        displayName: sanitiseName(join.display_name) || shortPeer(senderId),
        publicKey: record.header.sender_key,
        transports: Array.isArray(join.transports) ? join.transports : [],
        joinedAt: record.header.timestamp,
        isCreator,
        isSelf: senderId === options.selfPeerId,
        hasLeft: false,
        admitted,
        ...(admissionError ? { admissionError } : {}),
      });
    } else if (record.header.type === "profile") {
      const existing = participants.get(senderId);
      if (!existing) continue;
      const profile = payload as ProfilePayload;
      existing.displayName = sanitiseName(profile.display_name) || existing.displayName;
      if (Array.isArray(profile.transports)) existing.transports = profile.transports;
    } else if (record.header.type === "leave") {
      const existing = participants.get(senderId);
      if (existing) existing.hasLeft = true;
      void (payload as LeavePayload);
    }
  }

  const messages: ChatMessage[] = [];
  for (const { record, payload } of decoded) {
    if (record.header.type !== "text") continue;
    const text = (payload as TextPayload).text;
    if (typeof text !== "string") continue;
    const sender = participants.get(record.header.sender_id);
    messages.push({
      messageId: record.header.message_id,
      conversationId,
      senderId: record.header.sender_id,
      senderName: sender?.displayName ?? shortPeer(record.header.sender_id),
      text,
      timestamp: record.header.timestamp,
      seq: record.header.seq,
      lamport: record.header.lamport,
      mine: record.header.sender_id === options.selfPeerId,
      fromAdmittedPeer: sender?.admitted ?? false,
    });
  }

  // Stable, human-sensible order: creator first, then by join time. Every
  // node computes the same order from the same records.
  const orderedParticipants = [...participants.values()].sort((a, b) => {
    if (a.isCreator !== b.isCreator) return a.isCreator ? -1 : 1;
    if (a.joinedAt !== b.joinedAt) return a.joinedAt < b.joinedAt ? -1 : 1;
    return a.peerId < b.peerId ? -1 : 1;
  });

  return {
    conversationId,
    creatorId,
    inviteSecret: inviteSecretB64,
    participants: orderedParticipants,
    messages,
    revokedInvites: [...revoked.keys()],
    undecryptable,
  };
}

function checkAdmission(
  record: SignedRecord,
  join: JoinPayload,
  context: {
    conversationId: string;
    inviteSecret: Buffer | null;
    revoked: Map<string, Set<string>>;
  },
): { admitted: boolean; error?: string } {
  if (!join.invite) return { admitted: false, error: "join carried no invite" };
  if (!context.inviteSecret) {
    // We have not seen the genesis record yet. Do not admit on faith; the
    // caller re-computes after sync and this resolves itself.
    return { admitted: false, error: "invite secret not yet known (syncing)" };
  }
  const grandfathered = context.revoked.get(join.invite.nonce);
  if (grandfathered && !grandfathered.has(record.header.sender_id)) {
    return { admitted: false, error: "invite was revoked" };
  }
  try {
    verifyInvite(join.invite, {
      conversationId: context.conversationId,
      inviteSecret: context.inviteSecret,
      // Judged against the join record's own timestamp so every node reaches
      // the same verdict. The live admission check in the P2P handshake uses
      // the local clock and is the one that stops a backdated replay.
      now: Date.parse(record.header.timestamp),
      revoked: new Set(),
    });
    return { admitted: true };
  } catch (error) {
    const code = error instanceof ProtocolError ? error.code : "invite_invalid";
    return { admitted: false, error: code };
  }
}

// Control characters would corrupt terminal output and confuse the UI.
const CONTROL_CHARACTERS = new RegExp("[\\u0000-\\u001f\\u007f]", "g");

function sanitiseName(name: unknown): string {
  if (typeof name !== "string") return "";
  return name.replace(CONTROL_CHARACTERS, "").trim().slice(0, 64);
}

export function shortPeer(peerId: string): string {
  return peerId.length > 10 ? `${peerId.slice(0, 8)}...` : peerId;
}
