/**
 * LinkChat frames inside MIME.
 *
 * SMTP here is plumbing, not email. A LinkChat message is not a human-readable
 * body with a subject line that means something; it is a signed frame carried
 * as an `application/linkchat+json` part. The human-readable text part exists
 * only so that a person who receives one in a normal mail client is not
 * baffled, and nothing in the protocol reads it.
 *
 * Headers are advisory routing aids. Every security decision is made on the
 * signed frame in the body, never on a header — headers are trivially forged
 * in transit.
 */
import { simpleParser, type ParsedMail } from "mailparser";
import { LINKCHAT_MEDIA_TYPE, PROTOCOL_VERSION, type SignedFrame } from "../../protocol/types.ts";

export const HEADER_PROTOCOL = "X-LinkChat-Protocol";
export const HEADER_CONVERSATION = "X-LinkChat-Conversation";
export const HEADER_SENDER = "X-LinkChat-Sender";
export const HEADER_KIND = "X-LinkChat-Kind";

export type LinkChatMail = {
  subject: string;
  text: string;
  headers: Record<string, string>;
  attachmentFilename: string;
  contentType: string;
  content: Buffer;
};

export function frameToMail(frame: SignedFrame): LinkChatMail {
  const conversationId =
    "conversation_id" in frame.frame ? String(frame.frame.conversation_id) : "unknown";
  return {
    subject: `[LinkChat] ${frame.frame.kind} ${conversationId}`,
    text:
      "This is a LinkChat protocol message, not correspondence.\n" +
      "The payload is the application/linkchat+json part; this text is ignored by the protocol.\n" +
      `Conversation: ${conversationId}\nFrame: ${frame.frame.kind}\n`,
    headers: {
      [HEADER_PROTOCOL]: PROTOCOL_VERSION,
      [HEADER_CONVERSATION]: conversationId,
      [HEADER_SENDER]: frame.sender_id,
      [HEADER_KIND]: frame.frame.kind,
    },
    attachmentFilename: "linkchat.json",
    contentType: LINKCHAT_MEDIA_TYPE,
    content: Buffer.from(JSON.stringify(frame), "utf8"),
  };
}

/**
 * Pull the frame back out of a received message. Returns null when the mail
 * is not LinkChat at all — a node's mailbox may well receive ordinary email,
 * and that is not an error.
 */
export function mailToFrame(mail: ParsedMail): SignedFrame | null {
  for (const attachment of mail.attachments ?? []) {
    if (attachment.contentType !== LINKCHAT_MEDIA_TYPE) continue;
    try {
      return JSON.parse(attachment.content.toString("utf8")) as SignedFrame;
    } catch {
      return null;
    }
  }
  return null;
}

export async function parseMail(raw: Buffer | string): Promise<ParsedMail> {
  return await simpleParser(raw);
}

export async function rawMailToFrame(raw: Buffer | string): Promise<SignedFrame | null> {
  return mailToFrame(await parseMail(raw));
}
