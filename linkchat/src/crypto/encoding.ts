/**
 * Encoding helpers shared by every layer.
 *
 * Canonical JSON matters more than it looks: signatures are computed over
 * bytes, so both sides must agree on exactly which bytes a message header
 * serialises to. `canonicalJson` fixes key order and rejects the values
 * (undefined, NaN, Infinity) that would serialise ambiguously.
 */

export function toBase64Url(bytes: Uint8Array | Buffer): string {
  return Buffer.from(bytes).toString("base64url");
}

export function fromBase64Url(text: string): Buffer {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) {
    throw new Error("not base64url");
  }
  return Buffer.from(text, "base64url");
}

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** Crockford base32 — used for human-shareable ids (no I, L, O, U). */
export function toBase32(bytes: Uint8Array): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += CROCKFORD[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += CROCKFORD[(value << (5 - bits)) & 31];
  return out;
}

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/**
 * Deterministic JSON: object keys sorted, no whitespace, no lossy values.
 * Two peers running different implementations must produce identical bytes
 * for the same logical object, or every signature check fails.
 */
export function canonicalJson(value: JsonValue): string {
  return JSON.stringify(canonicalise(value));
}

function canonicalise(value: JsonValue): JsonValue {
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite number is not canonicalisable");
    return value;
  }
  if (Array.isArray(value)) return value.map(canonicalise);
  if (typeof value === "object") {
    const out: { [key: string]: JsonValue } = {};
    for (const key of Object.keys(value).sort()) {
      const entry = value[key];
      if (entry === undefined) continue;
      out[key] = canonicalise(entry);
    }
    return out;
  }
  throw new Error(`value of type ${typeof value} is not canonicalisable`);
}

export function canonicalBytes(value: JsonValue): Buffer {
  return Buffer.from(canonicalJson(value), "utf8");
}
