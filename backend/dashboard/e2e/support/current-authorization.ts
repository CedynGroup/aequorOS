import path from "node:path";
import { DatabaseSync } from "node:sqlite";

const DATABASE = path.resolve(__dirname, "../.tmp/e2e.db");

/** Read the disposable fixture's current generation; never guess after a mutation. */
export function currentAuthorizationVersion(
  userId: string,
  organizationId: string,
  database = DATABASE,
): number {
  const db = new DatabaseSync(database, { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout=5000");
    const version = db
      .prepare(
        "SELECT authorization_version FROM users WHERE id=? AND organization_id=?",
      )
      .get(userId.replaceAll("-", ""), organizationId)?.authorization_version;
    if (
      typeof version !== "number" ||
      !Number.isSafeInteger(version) ||
      version < 1
    ) {
      throw new Error(
        "Missing valid authorization version for the fixture principal",
      );
    }
    return version;
  } finally {
    db.close();
  }
}
