/**
 * Every cryptographic operation in LinkChat funnels through this file, and
 * every one of them is a call into Node's OpenSSL-backed `node:crypto`.
 * Nothing here invents a construction: Ed25519 for signatures, AES-256-GCM
 * for authenticated encryption, HMAC-SHA256 for invite tokens, scrypt for
 * passphrase-derived key wrapping.
 */
import {
  createCipheriv,
  createDecipheriv,
  createHmac,
  createPrivateKey,
  createPublicKey,
  createHash,
  generateKeyPairSync,
  randomBytes,
  scryptSync,
  sign as nodeSign,
  timingSafeEqual,
  verify as nodeVerify,
} from "node:crypto";
import { fromBase64Url, toBase64Url } from "./encoding.ts";

export const AEAD_KEY_BYTES = 32;
export const AEAD_NONCE_BYTES = 12;
export const AEAD_TAG_BYTES = 16;

export type RawKeyPair = {
  /** 32-byte Ed25519 public key. */
  publicKey: Buffer;
  /** 32-byte Ed25519 private key seed. */
  privateKey: Buffer;
};

export function randomKey(bytes = AEAD_KEY_BYTES): Buffer {
  return randomBytes(bytes);
}

export function sha256(data: Uint8Array | string): Buffer {
  return createHash("sha256").update(data).digest();
}

// --- Ed25519 -------------------------------------------------------------
//
// Node hands out KeyObjects, not raw bytes. JWK export/import is the only
// documented route to the raw 32-byte values we want to persist, so all of
// the conversion is confined to these four functions.

export function generateSigningKeyPair(): RawKeyPair {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const pub = publicKey.export({ format: "jwk" }) as { x: string };
  const priv = privateKey.export({ format: "jwk" }) as { d: string };
  return { publicKey: fromBase64Url(pub.x), privateKey: fromBase64Url(priv.d) };
}

function privateKeyObject(seed: Buffer): ReturnType<typeof createPrivateKey> {
  const publicKey = publicKeyFromSeed(seed);
  return createPrivateKey({
    key: {
      kty: "OKP",
      crv: "Ed25519",
      d: toBase64Url(seed),
      x: toBase64Url(publicKey),
    },
    format: "jwk",
  });
}

function publicKeyObject(publicKey: Buffer): ReturnType<typeof createPublicKey> {
  return createPublicKey({
    key: { kty: "OKP", crv: "Ed25519", x: toBase64Url(publicKey) },
    format: "jwk",
  });
}

/** Recover the public key that belongs to a stored private seed. */
export function publicKeyFromSeed(seed: Buffer): Buffer {
  if (seed.length !== 32) throw new Error("ed25519 private key must be 32 bytes");
  // Node cannot import a bare seed without the matching x, so build a PKCS#8
  // DER wrapper (the seed is the whole key material) and read the public half
  // back out of the derived public KeyObject.
  const der = Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"),
    seed,
  ]);
  const key = createPrivateKey({ key: der, format: "der", type: "pkcs8" });
  const jwk = createPublicKey(key).export({ format: "jwk" }) as { x: string };
  return fromBase64Url(jwk.x);
}

export function sign(message: Uint8Array, privateSeed: Buffer): Buffer {
  return nodeSign(null, message, privateKeyObject(privateSeed));
}

export function verify(message: Uint8Array, signature: Uint8Array, publicKey: Buffer): boolean {
  if (publicKey.length !== 32 || signature.length !== 64) return false;
  try {
    return nodeVerify(null, message, publicKeyObject(publicKey), signature);
  } catch {
    return false;
  }
}

// --- AES-256-GCM ---------------------------------------------------------

export type SealedBox = {
  /** base64url 12-byte nonce */
  nonce: string;
  /** base64url ciphertext */
  ciphertext: string;
  /** base64url 16-byte authentication tag */
  tag: string;
};

export function seal(plaintext: Uint8Array, key: Buffer, aad: Uint8Array): SealedBox {
  if (key.length !== AEAD_KEY_BYTES) throw new Error("aead key must be 32 bytes");
  const nonce = randomBytes(AEAD_NONCE_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  cipher.setAAD(Buffer.from(aad));
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return {
    nonce: toBase64Url(nonce),
    ciphertext: toBase64Url(ciphertext),
    tag: toBase64Url(cipher.getAuthTag()),
  };
}

/** Returns null when the box fails authentication — callers must handle it. */
export function open(box: SealedBox, key: Buffer, aad: Uint8Array): Buffer | null {
  if (key.length !== AEAD_KEY_BYTES) throw new Error("aead key must be 32 bytes");
  try {
    const decipher = createDecipheriv("aes-256-gcm", key, fromBase64Url(box.nonce));
    decipher.setAAD(Buffer.from(aad));
    decipher.setAuthTag(fromBase64Url(box.tag));
    return Buffer.concat([decipher.update(fromBase64Url(box.ciphertext)), decipher.final()]);
  } catch {
    return null;
  }
}

// --- HMAC / constant-time comparison -------------------------------------

export function hmac(key: Buffer, message: Uint8Array | string): Buffer {
  return createHmac("sha256", key).update(message).digest();
}

export function constantTimeEquals(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a), Buffer.from(b));
}

// --- Passphrase key wrapping ---------------------------------------------

export type ScryptParams = { N: number; r: number; p: number };
export const DEFAULT_SCRYPT: ScryptParams = { N: 1 << 15, r: 8, p: 1 };

export function deriveKey(passphrase: string, salt: Buffer, params = DEFAULT_SCRYPT): Buffer {
  return scryptSync(passphrase, salt, AEAD_KEY_BYTES, {
    N: params.N,
    r: params.r,
    p: params.p,
    maxmem: 256 * 1024 * 1024,
  });
}

export { randomBytes };
