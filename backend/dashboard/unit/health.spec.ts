import { describe, expect, it, vi } from "vitest";
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { GET } from "../app/api/health/live/route";
import { config } from "../proxy";

// The matcher executes before Auth.js; keep its external session boundary out
// of this routing test. The production-server HTTP check exercises both.
vi.mock("@/auth", () => ({ auth: vi.fn() }));

describe("dashboard process liveness", () => {
  it("returns an uncached, unauthenticated process verdict", async () => {
    const response = GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      service: "aequoros-dashboard",
      status: "ok",
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it.each(["/api/health/live", "/api/health/live?monitor=deployment"])(
    "bypasses authentication for %s",
    (url) => {
      expect(
        unstable_doesMiddlewareMatch({ config, nextConfig: {}, url }),
      ).toBe(false);
    },
  );

  it.each([
    "/",
    "/api/health/ready",
    "/api/health/live/private",
    "/api/health/lively",
  ])("continues to protect %s", (url) => {
    expect(unstable_doesMiddlewareMatch({ config, nextConfig: {}, url })).toBe(
      true,
    );
  });
});
