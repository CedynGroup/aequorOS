import { expect } from "@playwright/test";
import { test } from "../e2e/support/story-identities";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { E2E_API_ORIGIN } from "../playwright.config";
import { E2E_PASSWORD, e2eEmail } from "../e2e/support/mint";
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
test.use({
  storyConfig: {
    roles: ["admin", "viewer"],
    ownedTenant: true,
  },
});
test("password sign-in grants self-service but no institution or directory authority", async ({
  request,
  storyIdentity,
}) => {
  const api = `${E2E_API_ORIGIN}/api/v1`;
  const login = await request.post(`${api}/auth/login`, {
    data: {
      email: e2eEmail("viewer"),
      password: E2E_PASSWORD,
    },
  });
  expect(login.status()).toBe(200);
  const headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  };
  const me = await request.get(`${api}/auth/me`, { headers });
  expect(me.status()).toBe(200);
  const profile = await me.json();
  expect(profile.organization_id).toBe(storyIdentity.organizationId);
  expect(profile.effective_authority.organization_capabilities).toEqual([]);
  expect(profile.effective_authority.institution_capabilities).toEqual([]);
  const evidence: unknown[] = [
    { path: "/auth/me", status: me.status(), body: profile },
  ];
  const update = await request.patch(`${api}/auth/me`, {
    headers,
    data: { theme: "system" },
  });
  expect(update.status()).toBe(200);
  expect((await update.json()).theme).toBe("system");
  for (const [route, status] of [
    ["/banks", 200],
    [`/banks/${storyIdentity.bankId}`, 404],
    ["/banks/BK-NONE0001", 404],
    ["/organization/users", 403],
  ] as const) {
    const response = await request.get(`${api}${route}`, { headers });
    expect(response.status()).toBe(status);
    const body = await response.json();
    if (route === "/banks") expect(body.banks).toEqual([]);
    evidence.push({ path: route, status: response.status(), body });
  }
  if (evidenceDir)
    writeFileSync(
      path.join(evidenceDir, "baseline-api.json"),
      JSON.stringify(evidence, null, 2),
    );
});
