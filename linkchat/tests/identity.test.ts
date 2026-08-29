import assert from "node:assert/strict";
import { test } from "node:test";
import { Identity, publicKeyMatchesPeerId, verifyWithPublicKey } from "../src/identity/identity.ts";
import { Keystore, PassphraseRequiredError, WrongPassphraseError } from "../src/identity/keystore.ts";
import { peerIdFromPublicKey } from "../src/crypto/ids.ts";
import { tempDir } from "./helpers.ts";

test("identity generation produces a self-authenticating peer id", () => {
  const identity = Identity.generate("Alice");
  assert.match(identity.peerId, /^p_[0-9A-HJKMNP-TV-Z]{16}$/);
  assert.equal(identity.publicKey.length, 32);
  assert.ok(publicKeyMatchesPeerId(identity.peerId, identity.publicKey));
});

test("two identities never collide", () => {
  const ids = new Set(Array.from({ length: 50 }, () => Identity.generate("x").peerId));
  assert.equal(ids.size, 50);
});

test("signatures verify, and tampered messages do not", () => {
  const identity = Identity.generate("Alice");
  const message = Buffer.from("the quick brown fox");
  const signature = identity.sign(message);
  assert.ok(verifyWithPublicKey(message, signature, identity.publicKey));
  assert.equal(verifyWithPublicKey(Buffer.from("the quick brown fix"), signature, identity.publicKey), false);
});

test("a signature from one identity does not verify under another key", () => {
  const alice = Identity.generate("Alice");
  const mallory = Identity.generate("Mallory");
  const signature = alice.sign(Buffer.from("hello"));
  assert.equal(verifyWithPublicKey(Buffer.from("hello"), signature, mallory.publicKey), false);
});

test("a corrupted signature is rejected rather than throwing", () => {
  const identity = Identity.generate("Alice");
  const signature = identity.sign(Buffer.from("hello"));
  signature[0] = signature[0]! ^ 0xff;
  assert.equal(verifyWithPublicKey(Buffer.from("hello"), signature, identity.publicKey), false);
  assert.equal(verifyWithPublicKey(Buffer.from("hello"), Buffer.alloc(10), identity.publicKey), false);
});

test("keys persist across restarts", () => {
  const dir = tempDir();
  try {
    const first = new Keystore(dir.path).loadOrCreate("Alice");
    const second = new Keystore(dir.path).loadOrCreate("Alice");
    assert.equal(first.peerId, second.peerId);
    assert.deepEqual(first.exportPrivateKey(), second.exportPrivateKey());
  } finally {
    dir.cleanup();
  }
});

test("a renamed identity keeps its key and its peer id", () => {
  const dir = tempDir();
  try {
    const first = new Keystore(dir.path).loadOrCreate("Alice");
    const renamed = new Keystore(dir.path).loadOrCreate("Alice Cooper");
    assert.equal(first.peerId, renamed.peerId);
    assert.equal(renamed.displayName, "Alice Cooper");
  } finally {
    dir.cleanup();
  }
});

test("a passphrase wraps the private key, and the wrong one fails", () => {
  const dir = tempDir();
  try {
    const protectedStore = new Keystore(dir.path, "correct horse battery staple");
    const identity = protectedStore.loadOrCreate("Alice");
    assert.ok(protectedStore.isPassphraseProtected());
    assert.equal(new Keystore(dir.path, "correct horse battery staple").load().peerId, identity.peerId);
    assert.throws(() => new Keystore(dir.path, "wrong").load(), WrongPassphraseError);
    assert.throws(() => new Keystore(dir.path).load(), PassphraseRequiredError);
  } finally {
    dir.cleanup();
  }
});

test("the peer id is a function of the public key alone", () => {
  const identity = Identity.generate("Alice");
  assert.equal(peerIdFromPublicKey(identity.publicKey), identity.peerId);
  assert.notEqual(peerIdFromPublicKey(Buffer.alloc(32, 1)), identity.peerId);
});
