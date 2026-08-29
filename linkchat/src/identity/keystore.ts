/**
 * On-disk keystore.
 *
 * The private key stays on the device. It is written 0600, and when
 * LINKCHAT_KEY_PASSPHRASE is set it is wrapped with scrypt + AES-256-GCM
 * before it touches the filesystem. Without a passphrase the key is stored in
 * the clear, exactly like an SSH key with no passphrase — the file mode is the
 * only protection, and `linkchat identity` says so out loud.
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fromBase64Url, toBase64Url } from "../crypto/encoding.ts";
import {
  DEFAULT_SCRYPT,
  deriveKey,
  open,
  randomBytes,
  seal,
  type SealedBox,
} from "../crypto/primitives.ts";
import { Identity } from "./identity.ts";

type StoredIdentity = {
  version: 1;
  peerId: string;
  publicKey: string;
  displayName: string;
  protection: "plaintext" | "scrypt-aes-256-gcm";
  privateKey?: string;
  wrapped?: { salt: string; params: typeof DEFAULT_SCRYPT; box: SealedBox };
};

export class PassphraseRequiredError extends Error {
  constructor() {
    super("identity is passphrase-protected; set LINKCHAT_KEY_PASSPHRASE");
    this.name = "PassphraseRequiredError";
  }
}

export class WrongPassphraseError extends Error {
  constructor() {
    super("LINKCHAT_KEY_PASSPHRASE does not decrypt this identity");
    this.name = "WrongPassphraseError";
  }
}

export class Keystore {
  readonly path: string;
  readonly #passphrase: string | undefined;

  constructor(dataDir: string, passphrase?: string) {
    this.path = join(dataDir, "identity.json");
    this.#passphrase = passphrase && passphrase.length > 0 ? passphrase : undefined;
  }

  exists(): boolean {
    return existsSync(this.path);
  }

  load(): Identity {
    const stored = JSON.parse(readFileSync(this.path, "utf8")) as StoredIdentity;
    if (stored.version !== 1) throw new Error(`unsupported keystore version ${stored.version}`);

    if (stored.protection === "plaintext") {
      if (!stored.privateKey) throw new Error("keystore is missing its private key");
      return new Identity(fromBase64Url(stored.privateKey), stored.displayName);
    }

    if (!stored.wrapped) throw new Error("keystore is missing its wrapped key");
    if (!this.#passphrase) throw new PassphraseRequiredError();
    const key = deriveKey(this.#passphrase, fromBase64Url(stored.wrapped.salt), stored.wrapped.params);
    const plaintext = open(stored.wrapped.box, key, Buffer.from(stored.peerId, "utf8"));
    if (!plaintext) throw new WrongPassphraseError();
    return new Identity(plaintext, stored.displayName);
  }

  save(identity: Identity): void {
    mkdirSync(dirname(this.path), { recursive: true });
    const base = {
      version: 1 as const,
      peerId: identity.peerId,
      publicKey: toBase64Url(identity.publicKey),
      displayName: identity.displayName,
    };

    let stored: StoredIdentity;
    if (this.#passphrase) {
      const salt = randomBytes(16);
      const key = deriveKey(this.#passphrase, salt, DEFAULT_SCRYPT);
      stored = {
        ...base,
        protection: "scrypt-aes-256-gcm",
        wrapped: {
          salt: toBase64Url(salt),
          params: DEFAULT_SCRYPT,
          // The peer id is bound in as AAD so a wrapped key cannot be moved
          // into another identity file and still open.
          box: seal(identity.exportPrivateKey(), key, Buffer.from(identity.peerId, "utf8")),
        },
      };
    } else {
      stored = {
        ...base,
        protection: "plaintext",
        privateKey: toBase64Url(identity.exportPrivateKey()),
      };
    }

    writeFileSync(this.path, `${JSON.stringify(stored, null, 2)}\n`, { mode: 0o600 });
    chmodSync(this.path, 0o600);
  }

  /** Load the device identity, generating one on first run. */
  loadOrCreate(displayName: string): Identity {
    if (this.exists()) {
      const existing = this.load();
      if (existing.displayName === displayName) return existing;
      const renamed = existing.withDisplayName(displayName);
      this.save(renamed);
      return renamed;
    }
    const identity = Identity.generate(displayName);
    this.save(identity);
    return identity;
  }

  isPassphraseProtected(): boolean {
    if (!this.exists()) return this.#passphrase !== undefined;
    const stored = JSON.parse(readFileSync(this.path, "utf8")) as StoredIdentity;
    return stored.protection !== "plaintext";
  }
}
