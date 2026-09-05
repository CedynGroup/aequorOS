import { readFileSync } from "node:fs";
import path from "node:path";

// support/ -> e2e/ -> dashboard/ -> backend/, the directory whose .env the
// FastAPI process loads.
const BACKEND_DIR = path.join(__dirname, "..", "..", "..");

/**
 * Refuse a storage-dependent journey before it can burn through its timeouts.
 *
 * The stack is hermetic in every respect EXCEPT object storage.
 * `StorageBackend` is `Literal["s3"]` — there is no filesystem mode — so the
 * backend needs a reachable S3/MinIO endpoint to persist the artifacts a
 * package produces when it is validated. On a developer machine that arrives
 * silently from the untracked `backend/.env`, which is why the suite passes
 * there and nowhere else.
 *
 * Without this refusal the package simply never leaves `generated`, so seven
 * tests sit waiting for a Validated badge, an artifact download, or a signing
 * workspace that will never appear, and each burns its full 60-second timeout.
 * That is ~7.7 minutes to be told nothing. Measured 2026-07-31: cold worktree
 * without storage 7 failed / 7.7 min; the same commit with only the S3_* keys
 * added, 20 passed / 1.6 min.
 */
export function requireObjectStorage(): void {
  // Deliberately NOT /health/ready. `storage_configured` returns true with
  // nothing configured at all — backend defaults to "s3", bucket and region
  // carry defaults, and a null endpoint_url short-circuits to true before the
  // credentials are ever considered. Readiness therefore reports storage "ok"
  // on a machine that cannot store a byte, which is its own bug and the reason
  // this check first failed to fire.
  //
  // Check the same source the backend reads instead: process.env (CI exports
  // them) falling back to backend/.env (how a developer machine supplies them).
  const required = ["S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY"];
  const fromEnvFile = new Set<string>();
  try {
    const dotenv = readFileSync(path.join(BACKEND_DIR, ".env"), "utf8");
    for (const line of dotenv.split("\n")) {
      const key = /^\s*([A-Z0-9_]+)\s*=\s*\S/.exec(line)?.[1];
      if (key) fromEnvFile.add(key);
    }
  } catch {
    // No .env at all — every key counts as missing, which is the CI case.
  }
  const missing = required.filter(
    (key) => !process.env[key] && !fromEnvFile.has(key),
  );
  if (missing.length === 0) return;
  throw new Error(
    [
      `e2e needs object storage; ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} unset.`,
      "",
      "Artifacts are persisted to S3/MinIO when a package is validated, and the",
      "backend has no filesystem storage mode. Without it packages stay at",
      '"generated", so the attestation and lifecycle journeys each wait out a',
      "60s timeout for a Validated badge that never arrives — seven failures",
      "and no usable message.",
      "",
      "Locally: backend/.env supplies S3_ENDPOINT / S3_ACCESS_KEY /",
      "S3_SECRET_KEY / S3_BUCKET. Run this journey from a checkout that HAS it",
      "or start local MinIO; a git worktree does not inherit untracked files.",
      "In CI: run MinIO as a service container and pass the same four.",
    ].join("\n"),
  );
}
