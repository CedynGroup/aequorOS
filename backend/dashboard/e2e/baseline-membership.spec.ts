import { expect, test } from "@playwright/test";
import path from "path";
import { writeFileSync } from "fs";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { E2E_PASSWORD } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("fresh active member baseline", () => {
  test.use({ storageState: path.join(E2E_TMP, "invite_fresh.json") });

  test("deep links land in the shell with the module catalogue disabled", async ({
    page,
    browser,
  }) => {
    test.setTimeout(240_000);
    const productRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\//.test(request.url())) {
        productRequests.push(request.url());
      }
    });

    await page.goto("/");
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
    await page.goto("/liquidity/monitoring");
    await expect(page).toHaveURL(/\/liquidity\/monitoring$/);
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    await expect(
      page.getByText("Liquidity Monitoring · Confidential · View"),
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
    await expect(
      navigation.getByRole("link", { name: "Access", exact: true }),
    ).toHaveAttribute("href", "/access");
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
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    await page.goto("/basel/planning");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();

    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings$/);
    await expect(
      page.getByRole("heading", { name: "Your account", exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "baseline-membership-settings.png"),
        fullPage: true,
      });
    }

    await page.goto("/access");
    await expect(page).toHaveURL(/\/access\/my-access$/);
    for (const tab of ["Members", "Authentication", "Integration keys"]) {
      const link = page.getByRole("link", { name: tab, exact: true });
      await expect(link).toHaveAttribute("aria-disabled", "true");
    }
    const membersTab = page.getByRole("link", {
      name: "Members",
      exact: true,
    });
    await membersTab.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires Account · restricted · administer. Ask your organization owner or admin to grant it.",
      }),
    ).toBeVisible();

    await page.goto("/settings#members");
    await expect(page).toHaveURL(/\/access\/members$/);
    await page.goto("/settings#authentication");
    await expect(page).toHaveURL(/\/access\/authentication$/);
    await page.goto("/settings#integration-keys");
    await expect(page).toHaveURL(/\/access\/integration-keys$/);

    await page.goto("/fx");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    await expect(
      page.getByText("Foreign Exchange · Aggregated · View", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Request access" }).click();
    await page.getByLabel("Reason category").selectOption("project_engagement");
    await page
      .getByLabel("Detail")
      .fill("Supporting the treasury hedging review");
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("status")).toContainText(
      "waiting for an organization owner",
    );
    await expect(
      page.getByRole("button", { name: "Request access" }),
    ).toHaveCount(0);

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

    const ownerContext = await browser.newContext({
      storageState: path.join(E2E_TMP, "admin.json"),
    });
    const ownerPage = await ownerContext.newPage();
    await ownerPage.goto("/access/members");
    await expect(
      ownerPage.getByText("Requested access", { exact: true }),
    ).toBeVisible();
    await expect(
      ownerPage.getByText("Foreign Exchange", { exact: false }),
    ).toBeVisible();
    await ownerPage.getByRole("button", { name: "Review request" }).click();
    await expect(ownerPage.getByLabel("Module")).toHaveValue("fx");
    await expect(ownerPage.getByLabel("Sensitivity")).toHaveValue("aggregated");
    await expect(ownerPage.getByLabel("Reason category")).toHaveValue(
      "project_engagement",
    );
    await ownerPage.getByRole("button", { name: "Review grant" }).click();
    await ownerPage.getByRole("button", { name: "Grant access" }).click();
    await expect(
      ownerPage.getByText("Grant created", { exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await ownerPage.screenshot({
        path: path.join(evidenceDir, "owner-approved-access-request.png"),
        fullPage: true,
      });
    }
    await ownerContext.close();
  });

  test("password sign-in grants self-service but no institution or directory authority", async ({
    request,
  }) => {
    const api = `${E2E_API_ORIGIN}/api/v1`;
    const login = await request.post(`${api}/auth/login`, {
      data: {
        email: "e2e.viewer@aequoros.example",
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
