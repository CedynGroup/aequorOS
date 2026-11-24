/** PKCE (RFC 7636) and random-value helpers for the OIDC code flow. */

import { createHash, randomBytes } from "node:crypto";

/** A URL-safe random string carrying `bytes` bytes of entropy. */
export function randomUrlSafe(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

/** A code verifier: 43–128 characters from the unreserved set (RFC 7636 §4.1). */
export function createCodeVerifier(): string {
  return randomUrlSafe(48); // 64 characters
}

/** The S256 code challenge for a verifier (RFC 7636 §4.2). */
export function codeChallengeS256(verifier: string): string {
  return createHash("sha256").update(verifier, "ascii").digest("base64url");
}

export function isValidCodeVerifier(value: string): boolean {
  return /^[A-Za-z0-9\-._~]{43,128}$/.test(value);
}
