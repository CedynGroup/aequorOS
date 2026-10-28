import { createServer } from "node:http";
import { once } from "node:events";
import type { Socket } from "node:net";
import { request } from "@playwright/test";
import { expect, test, vi } from "vitest";

const endpoint = vi.hoisted(() => ({ origin: "" }));
vi.mock("../playwright.config", () => ({
  get E2E_API_ORIGIN() {
    return endpoint.origin;
  },
}));
vi.mock("../e2e/support/mint", () => ({
  E2E_USERS: { admin: {} },
  mintBackendToken: async () => "fixture-token",
}));
import { biApi } from "../e2e/support/bi";

test("API calls remain independent when an earlier connection expires", async () => {
  const seen = new WeakSet<Socket>();
  const server = createServer((req, res) => {
    // Pin the expiry race: a reused connection closes before a response.
    if (seen.has(req.socket)) {
      req.socket.destroy();
      return;
    }
    seen.add(req.socket);
    req.resume();
    req.on("end", () => {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ method: req.method, route: req.url }));
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing port");
  endpoint.origin = `http://127.0.0.1:${address.port}`;
  const api = await request.newContext();
  try {
    for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE"]) {
      const result = await biApi(api, "admin", "/fixture", { method });
      expect(result.status).toBe(200);
      expect(result.body).toEqual({ method, route: "/api/v1/fixture" });
    }
  } finally {
    await api.dispose();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
