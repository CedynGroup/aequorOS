/**
 * Encrypted cookie payloads.
 *
 * AES-256-GCM under a key derived (HKDF-SHA256) from the portal's session
 * secret. The cookie name is bound in as additional authenticated data, so a
 * value sealed for one cookie cannot be replayed as another. Format:
 * `v1.<iv>.<ciphertext||tag>`, each part base64url.
 */

import { createCipheriv, createDecipheriv, hkdfSync, randomBytes } from "node:crypto";

const VERSION = "v1";
const IV_BYTES = 12;
const TAG_BYTES = 16;
export const MIN_SECRET_LENGTH = 32;

export function deriveKey(secret: string): Buffer {
  if (secret.length < MIN_SECRET_LENGTH) {
    throw new Error(`The session secret must be at least ${MIN_SECRET_LENGTH} characters.`);
  }
  return Buffer.from(hkdfSync("sha256", secret, "srg-portal-session", "cookie-encryption-v1", 32));
}

export function seal(value: unknown, key: Buffer, purpose: string): string {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(Buffer.from(purpose, "utf8"));
  const plaintext = Buffer.from(JSON.stringify(value), "utf8");
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final(), cipher.getAuthTag()]);
  return `${VERSION}.${iv.toString("base64url")}.${ciphertext.toString("base64url")}`;
}

/** Returns the payload, or null when the value is malformed, tampered or sealed for another purpose. */
export function unseal<T>(sealed: string | undefined | null, key: Buffer, purpose: string): T | null {
  if (!sealed) return null;
  const parts = sealed.split(".");
  if (parts.length !== 3 || parts[0] !== VERSION) return null;
  try {
    const iv = Buffer.from(parts[1] ?? "", "base64url");
    const data = Buffer.from(parts[2] ?? "", "base64url");
    if (iv.length !== IV_BYTES || data.length <= TAG_BYTES) return null;
    const decipher = createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAAD(Buffer.from(purpose, "utf8"));
    decipher.setAuthTag(data.subarray(data.length - TAG_BYTES));
    const plaintext = Buffer.concat([decipher.update(data.subarray(0, data.length - TAG_BYTES)), decipher.final()]);
    return JSON.parse(plaintext.toString("utf8")) as T;
  } catch {
    return null;
  }
}

/** The signed-in staff session, kept server-side in an encrypted httpOnly cookie. */
export type PortalSession = {
  mode: "oidc" | "mock";
  subject: string;
  name: string;
  email: string;
  /** The identity provider's ID token (OIDC mode); the API verifies it on every call. */
  id_token?: string;
  /** Mock mode only: the synthetic staff member's identity groups. */
  groups?: string[];
  /** Unix seconds. */
  exp: number;
  /** Unix seconds. */
  iat: number;
};

/** In-flight authorization request (state, nonce, PKCE verifier). */
export type OauthTransaction = {
  state: string;
  nonce: string;
  verifier: string;
  redirect_uri: string;
  return_to: string;
  /** Unix seconds. */
  exp: number;
};

export function nowSeconds(now: number = Date.now()): number {
  return Math.floor(now / 1000);
}

export function isExpired(payload: { exp: number }, now: number = Date.now()): boolean {
  return !Number.isFinite(payload.exp) || payload.exp <= nowSeconds(now);
}

export function isPortalSession(value: unknown): value is PortalSession {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    (v.mode === "oidc" || v.mode === "mock") &&
    typeof v.subject === "string" &&
    v.subject.length > 0 &&
    typeof v.name === "string" &&
    typeof v.email === "string" &&
    typeof v.exp === "number" &&
    typeof v.iat === "number" &&
    (v.mode === "mock" ? Array.isArray(v.groups) : typeof v.id_token === "string" && v.id_token.length > 0)
  );
}

/** Reads and validates a session cookie value; null when absent, invalid or expired. */
export function readSessionValue(raw: string | undefined, key: Buffer, cookieName: string, now: number = Date.now()): PortalSession | null {
  const value = unseal<unknown>(raw, key, cookieName);
  if (!isPortalSession(value)) return null;
  return isExpired(value, now) ? null : value;
}

/** Maximum session lifetime regardless of the ID token's own expiry. */
export const MAX_SESSION_SECONDS = 12 * 3600;

/** Session expiry: the ID token's expiry, capped at the portal's maximum. */
export function sessionExpiry(tokenExp: number, now: number = Date.now()): number | null {
  const current = nowSeconds(now);
  if (!Number.isFinite(tokenExp) || tokenExp <= current) return null;
  return Math.min(tokenExp, current + MAX_SESSION_SECONDS);
}
