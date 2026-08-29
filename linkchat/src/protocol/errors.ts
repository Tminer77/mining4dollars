/** Protocol-level rejections. Every one of these is a security decision. */
export type ProtocolErrorCode =
  | "bad_protocol_version"
  | "bad_signature"
  | "bad_peer_id"
  | "bad_encoding"
  | "undecryptable"
  | "replayed"
  | "stale"
  | "future_dated"
  | "wrong_conversation"
  | "invite_invalid"
  | "invite_expired"
  | "invite_revoked"
  | "not_a_participant"
  | "sequence_conflict";

export class ProtocolError extends Error {
  readonly code: ProtocolErrorCode;

  constructor(code: ProtocolErrorCode, message: string) {
    super(message);
    this.name = "ProtocolError";
    this.code = code;
  }
}
