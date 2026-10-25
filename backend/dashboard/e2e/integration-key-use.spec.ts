import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  IngestionBatchStartReadFromJSON,
  IntegrationKeyIssuedFromJSON,
  PushBatchStatusReadFromJSON,
} from "@aequoros/risk-service-api";
import { randomUUID } from "node:crypto";
import { rmSync } from "node:fs";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_BASE_URL, E2E_TMP } from "../playwright.config";
import { biApi } from "./support/bi";
import { writeStorageState } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";
import {
  DENIED_BANK,
  MACHINE_ADMIN,
  MACHINE_BANK,
  createMachineTenant,
  readMachineBank,
  removeMachineTenant,
} from "./support/machine-key-journey";

test.use({
  storageState: async ({}, provideState) => {
    try {
      createMachineTenant();
      await provideState(
        await writeStorageState(MACHINE_ADMIN, E2E_BASE_URL, E2E_TMP),
      );
    } finally {
      try {
        removeMachineTenant();
      } finally {
        rmSync(path.join(E2E_TMP, `${MACHINE_ADMIN}.json`), { force: true });
      }
    }
  },
});
test.beforeAll(() => requireObjectStorage());

function machineRequest(
  request: APIRequestContext,
  key: string,
  bank: string,
  suffix = "",
  data?: unknown,
) {
  return request.fetch(
    `${E2E_API_ORIGIN}/api/v1/banks/${bank}/push-batches${suffix}`,
    {
      method: data === undefined ? "GET" : "POST",
      headers: { Authorization: `Bearer ${key}` },
      data,
    },
  );
}

test("an issued machine key writes its bank's book, refuses a real sibling bank, and loses access when revoked", async ({
  page,
  request,
}, testInfo) => {
  test.info().annotations.push({
    type: "critical-outcome",
    description: "integration.key-scope-revocation",
  });
  const asOf = "2026-06-30";
  const empty = { accounts: [], batches: 0, pushEvents: 0, openedEvents: 0 };
  expect(readMachineBank(MACHINE_BANK, asOf)).toEqual(empty);
  expect(readMachineBank(DENIED_BANK, asOf)).toEqual(empty);

  // The denied institution exists and is visible to the administrator's real binding.
  const sibling = await biApi(request, MACHINE_ADMIN, `/banks/${DENIED_BANK}`);
  expect(sibling.status, JSON.stringify(sibling.body)).toBe(200);
  expect(sibling.body).toMatchObject({ id: DENIED_BANK });

  await page.goto("/access/integration-keys");
  await expect(
    page.getByRole("heading", { name: "Integration keys" }),
  ).toBeVisible();
  await expect(
    page.getByText(`A Machine Push Bank (${MACHINE_BANK})`),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Key label" })
    .fill("Synthetic nightly book");
  const issuedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/integration-keys") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Generate data push key" }).click();
  const response = await issuedResponse;
  expect(response.status()).toBe(201);
  const issued = IntegrationKeyIssuedFromJSON(await response.json());
  expect(issued.record.bankId).toBe(MACHINE_BANK);
  expect(issued.key).toMatch(/^aeq_live_/);
  let revoked = false;
  try {
    await expect(
      page.getByText(/Data push key generated — copy it now/),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "I have stored it securely — dismiss" })
      .click();

    const openPayload = () => ({
      as_of_date: asOf,
      idempotency_key: `machine-journey-${randomUUID()}`,
      reason: "Synthetic machine-principal journey",
    });
    const opened = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      "",
      openPayload(),
    );
    expect(opened.status()).toBe(201);
    const push = PushBatchStatusReadFromJSON(await opened.json());
    expect(push).toMatchObject({
      bankId: MACHINE_BANK,
      status: "staging",
      totalRecordsStaged: 0,
    });

    const refused = await machineRequest(
      request,
      issued.key,
      DENIED_BANK,
      "",
      openPayload(),
    );
    expect(refused.status()).toBe(404);
    expect(readMachineBank(DENIED_BANK, asOf)).toEqual(empty);

    const staged = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      `/${push.pushBatchId}/records`,
      {
        entities: {
          gl_account: [
            {
              source_reference: "KEY-CASH",
              account_code: "1000",
              name: "Synthetic cash",
              account_class: "ASSET",
              currency: "GHS",
              balance: "2500",
            },
            {
              source_reference: "KEY-EQUITY",
              account_code: "3000",
              name: "Synthetic equity",
              account_class: "EQUITY",
              currency: "GHS",
              balance: "2500",
            },
          ],
        },
      },
    );
    expect(staged.status()).toBe(200);
    expect(PushBatchStatusReadFromJSON(await staged.json())).toMatchObject({
      bankId: MACHINE_BANK,
      totalRecordsStaged: 2,
      pagesStaged: 1,
    });
    const committed = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      `/${push.pushBatchId}/commit`,
      {},
    );
    expect(committed.status()).toBe(201);
    const { batch } = IngestionBatchStartReadFromJSON(await committed.json());
    expect(batch).toMatchObject({
      bankId: MACHINE_BANK,
      recordsAccepted: 2,
      recordsError: 0,
      recordsBlocked: 0,
    });

    const status = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      `/${push.pushBatchId}`,
    );
    expect(status.status()).toBe(200);
    expect(PushBatchStatusReadFromJSON(await status.json())).toMatchObject({
      status: "committed",
      committedBatchId: batch.id,
      totalRecordsStaged: 2,
    });
    const persisted = readMachineBank(MACHINE_BANK, asOf);
    expect(persisted).toMatchObject({
      batches: 1,
      openedEvents: 1,
      pushEvents: 3,
    });
    expect(persisted.accounts).toHaveLength(2);
    expect(
      persisted.accounts.map((account) => [
        account.account_code,
        account.account_class,
        account.currency,
        Number(account.balance),
      ]),
    ).toEqual([
      ["1000", "ASSET", "GHS", 2500],
      ["3000", "EQUITY", "GHS", 2500],
    ]);
    for (const account of persisted.accounts) {
      expect(account.ingestion_batch_id).toBe(batch.id.replaceAll("-", ""));
      expect(account.lineage_id).toMatch(/^[a-f0-9]{32}$/);
    }

    const row = page
      .getByRole("listitem")
      .filter({ hasText: "Synthetic nightly book" });
    page.once("dialog", (dialog) =>
      dialog.accept("Synthetic key revocation proof"),
    );
    const revocation = page.waitForResponse((reply) =>
      reply.url().endsWith(`/integration-keys/${issued.record.id}/revoke`),
    );
    await row.getByRole("button", { name: "Revoke" }).click();
    expect((await revocation).status()).toBe(200);
    revoked = true;
    await expect(row.getByText("Revoked", { exact: true })).toBeVisible();

    const revokedOpen = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      "",
      openPayload(),
    );
    expect(revokedOpen.status()).toBe(401);
    const revokedRead = await machineRequest(
      request,
      issued.key,
      MACHINE_BANK,
      `/${push.pushBatchId}`,
    );
    expect(revokedRead.status()).toBe(401);
    expect(readMachineBank(MACHINE_BANK, asOf)).toEqual(persisted);
    expect(readMachineBank(DENIED_BANK, asOf)).toEqual(empty);
    await testInfo.attach("bank-scoped key revoked", {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  } finally {
    if (!revoked) {
      const cleanup = await biApi(
        request,
        MACHINE_ADMIN,
        `/integration-keys/${issued.record.id}/revoke`,
        {
          method: "POST",
          data: {
            reason: "Revoke synthetic journey credential during cleanup",
          },
        },
      );
      expect(cleanup.status, JSON.stringify(cleanup.body)).toBe(200);
    }
  }
});
