export const SQLITE_READ_BUSY = Symbol("sqlite-read-busy");

/** Keep the poll's deadline authoritative while a live writer holds a lock. */
export function readSqliteForPoll<T>(
  read: () => T,
): T | typeof SQLITE_READ_BUSY {
  try {
    return read();
  } catch (error) {
    if (
      error instanceof Error &&
      "code" in error &&
      error.code === "ERR_SQLITE_ERROR" &&
      "errcode" in error &&
      typeof error.errcode === "number"
    ) {
      // SQLite's primary code is the low byte, including for extended codes.
      const code = error.errcode & 0xff;
      if (code === 5 || code === 6) return SQLITE_READ_BUSY; // SQLITE_BUSY / SQLITE_LOCKED
    }
    throw error;
  }
}
