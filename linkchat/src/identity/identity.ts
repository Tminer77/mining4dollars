/**
 * Identity: a device-local Ed25519 key pair, and nothing else.
 *
 * An email address is a *delivery hint* attached to an identity later, never
 * the identity itself. Two installations with the same email are two peers;
 * one installation that changes email is still the same peer.
 */
import { peerIdFromPublicKey } from "../crypto/ids.ts";
import { toBase64Url } from "../crypto/encoding.ts";
import { generateSigningKeyPair, publicKeyFromSeed, sign, verify } from "../crypto/primitives.ts";

export type PublicIdentity = {
  peerId: string;
  /** base64url Ed25519 public key */
  publicKey: string;
  /** Free-text label the user picked. Not authenticated by anything. */
  displayName: string;
};

export class Identity {
  readonly peerId: string;
  readonly publicKey: Buffer;
  readonly displayName: string;
  readonly #privateKey: Buffer;

  constructor(privateKey: Buffer, displayName: string, publicKey?: Buffer) {
    this.#privateKey = privateKey;
    this.publicKey = publicKey ?? publicKeyFromSeed(privateKey);
    this.peerId = peerIdFromPublicKey(this.publicKey);
    this.displayName = displayName;
  }

  static generate(displayName: string): Identity {
    const pair = generateSigningKeyPair();
    return new Identity(pair.privateKey, displayName, pair.publicKey);
  }

  /** The private key never leaves the process except through the keystore. */
  exportPrivateKey(): Buffer {
    return Buffer.from(this.#privateKey);
  }

  sign(message: Uint8Array): Buffer {
    return sign(message, this.#privateKey);
  }

  toPublic(): PublicIdentity {
    return {
      peerId: this.peerId,
      publicKey: toBase64Url(this.publicKey),
      displayName: this.displayName,
    };
  }

  withDisplayName(displayName: string): Identity {
    return new Identity(this.#privateKey, displayName, this.publicKey);
  }
}

/**
 * Check a claimed peer id against the key that supposedly backs it. Anything
 * that accepts a peer's public key from the network must call this first,
 * otherwise a peer could claim someone else's id.
 */
export function publicKeyMatchesPeerId(peerId: string, publicKey: Buffer): boolean {
  return peerIdFromPublicKey(publicKey) === peerId;
}

export function verifyWithPublicKey(
  message: Uint8Array,
  signature: Uint8Array,
  publicKey: Buffer,
): boolean {
  return verify(message, signature, publicKey);
}
