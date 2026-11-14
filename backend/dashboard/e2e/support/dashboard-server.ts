import type { ChildProcess } from "node:child_process";
import net from "node:net";
import { setTimeout as delay } from "node:timers/promises";

export async function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not reserve an IPv4 port."));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

/**
 * Wait for an isolated development server to render `/login`.
 *
 * Each probe is bounded: a request accepted while the server is still
 * compiling can stall, and an unbounded probe would hold the loop until the
 * test's own timeout, which reports nothing about why. The deadline sits inside
 * the callers' test budget so its error is the one that surfaces.
 */
export async function waitForLoginPage(origin: string, child: ChildProcess) {
  // The deadline is monotonic: a wall-clock jump mid-boot must not extend or
  // cut the probe budget.
  const deadline = performance.now() + 100_000;
  await new Promise<void>((resolve, reject) => {
    const probe = async (): Promise<void> => {
      if (child.exitCode !== null) {
        reject(
          new Error(`Isolated dashboard exited with code ${child.exitCode}.`),
        );
        return;
      }
      try {
        const response = await fetch(`${origin}/login`, {
          signal: AbortSignal.timeout(15_000),
        });
        if (response.ok) {
          resolve();
          return;
        }
      } catch {
        // Not bound yet, or this probe outlived its bound while compiling.
      }
      if (performance.now() >= deadline) {
        reject(new Error("Timed out waiting for the isolated dashboard."));
        return;
      }
      setTimeout(() => void probe(), 250);
    };
    void probe();
  });
}

export async function stopServer(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || !child.pid) return;
  process.kill(-child.pid, "SIGTERM");
  // Bounded escalation, not a synchronization wait: give the process five
  // seconds to honor SIGTERM, then SIGKILL whatever is left.
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    delay(5_000),
  ]);
  if (child.exitCode === null && child.pid) {
    // A signal exit resolves the race with exitCode still null; the group may
    // already be gone, so only ESRCH is tolerated — anything else is real.
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
}
