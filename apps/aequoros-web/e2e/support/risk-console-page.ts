import { expect, type Page } from "playwright/test";

import { demoTenant, northstarCase, type DemoTenant } from "./test-data";

export class RiskConsolePage {
  private readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  async seedTenantStorage(tenant: DemoTenant = demoTenant) {
    await this.page.addInitScript((seed) => {
      window.localStorage.setItem("aequoros.orgId", seed.orgId);
      window.localStorage.setItem("aequoros.userId", seed.userId);
      window.localStorage.removeItem("aequoros.caseId");
    }, tenant);
  }

  async gotoQueue() {
    await this.page.goto("/cases");
  }

  async gotoSelectedCase(tab = "overview") {
    await this.page.goto(
      `/cases/${northstarCase.id}?tab=${tab}&archived=false`,
    );
  }

  async expectQueueLoaded() {
    await expect(
      this.page.getByRole("heading", { name: "Case Queue" }),
    ).toBeVisible();
    await expect(this.northstarButton()).toBeVisible();
  }

  async openNorthstarFromQueue() {
    await this.northstarButton().click();
    await expect(this.page).toHaveURL(new RegExp(`/cases/${northstarCase.id}`));
  }

  async expectNorthstarWorkspace() {
    await expect(
      this.page.getByRole("heading", { name: "Case Detail" }),
    ).toBeVisible();
    await expect(
      this.page.getByRole("heading", { name: northstarCase.title }),
    ).toBeVisible();
  }

  async searchCases(query: string) {
    await this.page.getByPlaceholder("Search cases").fill(query);
  }

  async selectNorthstarInQueue() {
    await this.page.getByLabel(`Select ${northstarCase.title}`).click();
  }

  async expectFinancialSections() {
    for (const section of [
      "Institutions",
      "Accounts",
      "Reporting Periods",
      "Balances",
      "Obligations",
    ]) {
      await expect(this.page.getByText(section, { exact: true })).toBeVisible();
    }
  }

  async expectMobileOverview() {
    await this.page.setViewportSize({ width: 390, height: 844 });
    await this.page.reload();

    await expect(
      this.page.getByRole("button", { name: "Show case queue" }),
    ).toBeVisible();
    const evidenceDir = process.env.NO_MISTAKES_EVIDENCE_DIR;
    if (evidenceDir) {
      await this.page.screenshot({
        path: `${evidenceDir}/mobile-top-bar-queue-toggle-overlap.png`,
        fullPage: true,
      });
    }
    await this.page.getByRole("button", { name: "Show case queue" }).click();
    await expect(
      this.page.getByRole("heading", { name: "Case Queue" }),
    ).toBeVisible();
    await expect(
      this.page.getByRole("heading", { name: northstarCase.title }),
    ).toBeVisible();
    await expect(
      this.page.getByRole("tab", { name: "Financial Workspace" }),
    ).toBeVisible();
  }

  northstarButton() {
    return this.page.getByRole("button", { name: northstarCase.title });
  }
}
