import { expect, test } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test("deployment monitor reaches process liveness without a session", async ({
  request,
}) => {
  const response = await request.get("/api/health/live", { maxRedirects: 0 });

  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({
    service: "aequoros-dashboard",
    status: "ok",
  });
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(response.headers()["location"]).toBeUndefined();
  expect(response.headers()["set-cookie"]).toBeUndefined();
});

test("neighboring routes still require authentication", async ({ request }) => {
  for (const route of ["/", "/api/health/ready", "/api/health/live/private"]) {
    const response = await request.get(route, { maxRedirects: 0 });

    expect(response.status()).toBe(307);
    expect(
      new URL(response.headers()["location"], response.url()).pathname,
    ).toBe("/login");
  }
});
