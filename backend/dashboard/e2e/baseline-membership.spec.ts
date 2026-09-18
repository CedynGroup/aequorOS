import { expect, test } from "@playwright/test";
import path from "path";
import { writeFileSync } from "fs";
import { E2E_API_ORIGIN } from "../playwright.config";
import { E2E_PASSWORD } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("fresh active member baseline", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("deep links land in the shell with the module catalogue disabled", async ({
    page,
  }) => {
    const productRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\//.test(request.url())) {
        productRequests.push(request.url());
      }
    });

    await page.goto("/login");
    // Let initial session requests settle before requesting a sign-in CSRF
    // token; cold dev compilation can otherwise race their cookie responses.
    await page.waitForLoadState("networkidle");
    await page
      .getByLabel("Email", { exact: true })
      .fill("e2e.invite_fresh@aequoros.example");
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await page.goto("/liquidity/monitoring");
    await expect(page).toHaveURL(/\/$/);
    await expect(
      page.getByText("No authorized institutions yet", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Ask your organization owner to grant access to an institution.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(
      page.getByText("No institution selected", { exact: true }),
    ).toBeVisible();

    const navigation = page.getByRole("navigation");
    for (const name of [
      "Command Center",
      "Risk & Limits",
      "Markets",
      "IRRBB",
      "Liquidity",
      "FX",
      "Basel Capital",
      "Data Engine",
      "Regulatory Reporting",
    ]) {
      await expect(
        navigation.getByRole("link", { name, exact: true }),
      ).toHaveAttribute("aria-disabled", "true");
    }

    const liquidity = navigation.getByRole("link", {
      name: "Liquidity",
      exact: true,
    });
    await liquidity.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires Liquidity Monitoring · Aggregated · View. Ask your organization owner or admin to grant it.",
      }),
    ).toBeVisible();

    await expect(
      navigation.getByRole("link", { name: "Settings", exact: true }),
    ).toHaveAttribute("href", "/settings");
    expect(productRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-membership-shell.png"),
        fullPage: true,
      });
    }

    await page.keyboard.press("Control+k");
    await page
      .getByPlaceholder("Search modules, screens, reports…")
      .fill("Liquidity Stress");
    const stress = page.getByRole("link", {
      name: "Liquidity — Stress Scenarios",
      exact: true,
    });
    await expect(stress).toHaveAttribute("aria-disabled", "true");
    await stress.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires Liquidity Monitoring · Confidential · View and Risk & Limits · Confidential · View. Ask your organization owner or admin to grant them.",
      }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-stress-tooltip.png"),
        fullPage: true,
      });
    }
    await page
      .getByPlaceholder("Search modules, screens, reports…")
      .fill("IRRBB Rate Scenario Workbench");
    const workbench = page.getByRole("link", {
      name: "IRRBB — Rate Scenario Workbench",
      exact: true,
    });
    await expect(workbench).toHaveAttribute("aria-disabled", "true");
    await workbench.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires IRRBB · Confidential · View. Ask your organization owner or admin to grant it.",
      }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-irrbb-workbench-tooltip.png"),
        fullPage: true,
      });
    }
    await page.keyboard.press("Escape");
    await page.goto("/irr/scenarios");
    await expect(page).toHaveURL(/\/$/);
    await page.goto("/basel/planning");
    await expect(page).toHaveURL(/\/$/);

    await navigation
      .getByRole("link", { name: "Settings", exact: true })
      .click();
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expect(
      page.getByRole("heading", { name: "Profile & preferences", exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-membership-settings.png"),
        fullPage: true,
      });
    }

    for (const route of [
      "/does-not-exist",
      "/liquidity/unknown",
      "/data-engine/batches/00000000-0000-0000-0000-000000000000",
      "/data-engine/batches/foreign-batch",
    ]) {
      await page.goto(route);
      await expect(page.getByText(/404|not found/i).first()).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      await expect(
        page.getByText("No authorized institutions yet", { exact: true }),
      ).toHaveCount(0);
    }
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-object-404.png"),
        fullPage: true,
      });
    }
  });

  test("password sign-in grants self-service but no institution or directory authority", async ({
    request,
  }) => {
    const api = `${E2E_API_ORIGIN}/api/v1`;
    const login = await request.post(`${api}/auth/login`, {
      data: {
        email: "e2e.invite_fresh@aequoros.example",
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
    expect(profile.organization_id).toBe("OR-DEM00001");
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
      ["/banks/BK-SAMP0001", 404],
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
});
