/**
 * File-backed storage.
 *
 *   <dataDir>/identity.json                       the device key (see keystore)
 *   <dataDir>/conversations/<cid>/meta.json       key material + settings
 *   <dataDir>/conversations/<cid>/log.jsonl       append-only record log
 *
 * The log is append-only JSONL because that is what the data model actually
 * is — a replicated log — and because a crash mid-write can lose at most the
 * last line rather than corrupting an index. SQLite is the obvious next step
 * once queries outgrow "read it all"; the port above exists so that swap
 * touches one file.
 *
 * When a storage key is supplied (derived from the device identity, so a
 * passphrase-protected identity protects these too), the conversation key and
 * invite secret in meta.json are sealed with AES-256-GCM. Record *bodies* are
 * already end-to-end ciphertext; record headers are not, so who-talked-to-whom
 * metadata is readable by anyone who can read the directory.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fromBase64Url, toBase64Url } from "../crypto/encoding.ts";
import { hmac, open, seal, type SealedBox } from "../crypto/primitives.ts";
import type { SignedRecord } from "../protocol/types.ts";
import type { ConversationStorage, StoredConversationMeta } from "./types.ts";

type SecretFields = { conversation_key: string; invite_secret: string | null };
type OnDiskMeta =
  | (StoredConversationMeta & { protection: "plaintext" })
  | (Omit<StoredConversationMeta, keyof SecretFields> & {
      protection: "aes-256-gcm";
      secrets: SealedBox;
    });

/** Bind storage encryption to the device key without ever storing that key. */
export function deriveStorageKey(privateKey: Buffer): Buffer {
  return hmac(privateKey, "linkchat/1 storage-key");
}

export class FileStorage implements ConversationStorage {
  readonly #root: string;
  readonly #storageKey: Buffer | undefined;

  constructor(dataDir: string, storageKey?: Buffer) {
    this.#root = join(dataDir, "conversations");
    this.#storageKey = storageKey;
    mkdirSync(this.#root, { recursive: true });
  }

  #dir(conversationId: string): string {
    if (conversationId.includes("/") || conversationId.includes("..")) {
      throw new Error(`refusing to use '${conversationId}' as a directory name`);
    }
    return join(this.#root, conversationId);
  }

  listMeta(): StoredConversationMeta[] {
    if (!existsSync(this.#root)) return [];
    const out: StoredConversationMeta[] = [];
    for (const entry of readdirSync(this.#root, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const meta = this.loadMeta(entry.name);
      if (meta) out.push(meta);
    }
    return out.sort((a, b) => a.created_at.localeCompare(b.created_at));
  }

  loadMeta(conversationId: string): StoredConversationMeta | null {
    const path = join(this.#dir(conversationId), "meta.json");
    if (!existsSync(path)) return null;
    const stored = JSON.parse(readFileSync(path, "utf8")) as OnDiskMeta;
    if (stored.protection === "plaintext") {
      const { protection: _p, ...meta } = stored;
      return meta;
    }
    if (!this.#storageKey) {
      throw new Error(`conversation ${conversationId} is encrypted but no storage key was supplied`);
    }
    const plaintext = open(
      stored.secrets,
      this.#storageKey,
      Buffer.from(stored.conversation_id, "utf8"),
    );
    if (!plaintext) {
      throw new Error(`conversation ${conversationId} did not decrypt with this device key`);
    }
    const { protection: _p, secrets: _s, ...rest } = stored;
    return { ...rest, ...(JSON.parse(plaintext.toString("utf8")) as SecretFields) };
  }

  saveMeta(meta: StoredConversationMeta): void {
    const dir = this.#dir(meta.conversation_id);
    mkdirSync(dir, { recursive: true });
    let payload: OnDiskMeta;
    if (this.#storageKey) {
      const { conversation_key, invite_secret, ...rest } = meta;
      payload = {
        ...rest,
        protection: "aes-256-gcm",
        secrets: seal(
          Buffer.from(JSON.stringify({ conversation_key, invite_secret }), "utf8"),
          this.#storageKey,
          Buffer.from(meta.conversation_id, "utf8"),
        ),
      };
    } else {
      payload = { ...meta, protection: "plaintext" };
    }
    writeFileSync(join(dir, "meta.json"), `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
  }

  readRecords(conversationId: string): SignedRecord[] {
    const path = join(this.#dir(conversationId), "log.jsonl");
    if (!existsSync(path)) return [];
    const out: SignedRecord[] = [];
    for (const line of readFileSync(path, "utf8").split("\n")) {
      if (!line.trim()) continue;
      try {
        out.push(JSON.parse(line) as SignedRecord);
      } catch {
        // A truncated final line is the expected shape of an interrupted
        // write; drop it rather than refusing to open the conversation.
      }
    }
    return out;
  }

  appendRecords(conversationId: string, records: SignedRecord[]): void {
    if (records.length === 0) return;
    const dir = this.#dir(conversationId);
    mkdirSync(dir, { recursive: true });
    const lines = records.map((record) => JSON.stringify(record)).join("\n");
    appendFileSync(join(dir, "log.jsonl"), `${lines}\n`, { mode: 0o600 });
  }
}

export { toBase64Url, fromBase64Url };
