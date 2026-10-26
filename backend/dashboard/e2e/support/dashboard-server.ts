import type { ChildProcess } from "node:child_process";
import net from "node:net";

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
  const deadline = Date.now() + 100_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Isolated dashboard exited with code ${child.exitCode}.`);
    }
    try {
      const response = await fetch(`${origin}/login`, {
        signal: AbortSignal.timeout(15_000),
      });
      if (response.ok) return;
    } catch {
      // Not bound yet, or this probe outlived its bound while compiling.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Timed out waiting for the isolated dashboard.");
}

export async function stopServer(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || !child.pid) return;
  process.kill(-child.pid, "SIGTERM");
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    new Promise<void>((resolve) =>
      setTimeout(() => {
        if (child.exitCode === null && child.pid) {
          process.kill(-child.pid, "SIGKILL");
        }
        resolve();
      }, 5_000),
    ),
  ]);
}
