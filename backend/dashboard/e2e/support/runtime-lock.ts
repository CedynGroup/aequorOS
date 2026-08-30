import { randomUUID } from "node:crypto";
import {
  mkdirSync,
  readFileSync,
  rmdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

const LOCK_INHERITED = "E2E_RUN_LOCK_INHERITED";
const LOCK_DIRECTORY = "playwright-run.lock";
const OWNER_FILE = "owner.json";

type LockOwner = {
  pid: number;
  token: string;
};

function readOwner(lockPath: string): LockOwner | undefined {
  try {
    return JSON.parse(
      readFileSync(path.join(lockPath, OWNER_FILE), "utf8"),
    ) as LockOwner;
  } catch {
    return undefined;
  }
}

function ownerIsRunning(owner: LockOwner): boolean {
  try {
    process.kill(owner.pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

function removeOwnedLock(lockPath: string, owner: LockOwner): boolean {
  if (readOwner(lockPath)?.token !== owner.token) return false;
  try {
    unlinkSync(path.join(lockPath, OWNER_FILE));
    rmdirSync(lockPath);
    return true;
  } catch {
    return false;
  }
}

export function acquireE2ERunLock(tmpPath: string): void {
  mkdirSync(tmpPath, { recursive: true });
  const lockPath = path.join(tmpPath, LOCK_DIRECTORY);
  const inheritedToken = process.env[LOCK_INHERITED];
  if (inheritedToken && readOwner(lockPath)?.token === inheritedToken) return;
  delete process.env[LOCK_INHERITED];

  while (true) {
    try {
      mkdirSync(lockPath);
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      const owner = readOwner(lockPath);
      if (owner && !ownerIsRunning(owner) && removeOwnedLock(lockPath, owner)) {
        continue;
      }
      const currentOwner = readOwner(lockPath);
      const ownerDetail = currentOwner
        ? ` (owner PID ${currentOwner.pid})`
        : "";
      throw new Error(
        `Another dashboard Playwright E2E run already owns this worktree's shared state${ownerDetail}. Wait for it to finish before starting another run.`,
      );
    }
  }

  const owner = { pid: process.pid, token: randomUUID() };
  try {
    writeFileSync(
      path.join(lockPath, OWNER_FILE),
      `${JSON.stringify(owner)}\n`,
      {
        flag: "wx",
      },
    );
  } catch (error) {
    rmdirSync(lockPath);
    throw error;
  }
  process.env[LOCK_INHERITED] = owner.token;

  process.once("exit", () => {
    removeOwnedLock(lockPath, owner);
  });
}
