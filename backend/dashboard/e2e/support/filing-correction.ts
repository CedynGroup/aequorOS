import {
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";
import {
  AttestationStatusReadFromJSON,
  CanonicalWithdrawalReadFromJSON,
  CanonicalWithdrawalScopeReadFromJSON,
  DataActivationReadFromJSON,
  IngestionBatchStartReadFromJSON,
  IngestionUploadReadFromJSON,
  MappingConfigReadFromJSON,
  PackageChainReadFromJSON,
  RegulatoryPackageListReadFromJSON,
  RegulatoryPackageReadFromJSON,
  RegulatoryRunReadFromJSON,
  SubmissionEventListReadFromJSON,
  type PackageChainRead,
  type RegulatoryPackageRead,
} from "@aequoros/risk-service-api";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { readFile } from "node:fs/promises";
import { E2E_API_ORIGIN, E2E_TMP } from "../../playwright.config";
import { biApi, SAMPLE_BANK_ID } from "./bi";
import { E2E_ORG_ID, E2E_USERS, mintBackendToken } from "./mint";
import { writeWorkbook } from "./worker-journey";

const packagePath = (id: string) =>
  `/banks/${SAMPLE_BANK_ID}/regulatory-packages/${id}`;

export async function uploadFilingBook(
  request: APIRequestContext,
  date: string,
  cash: "2000000" | "3000000",
  filename: string,
) {
  const sheets: Record<string, Record<string, string>[]> = {
    gl_accounts: [
      ["1001", "Cash vault", "ASSET", cash],
      ["1301", "Corporate loans", "ASSET", "200000"],
      ["2001", "Corporate deposits", "LIABILITY", "1000000"],
      [
        "3001",
        "Share capital",
        "EQUITY",
        cash === "2000000" ? "1200000" : "2200000",
      ],
    ].map(([code, name, accountClass, balance]) => ({
      source_reference: `FILING-${date}-${code}`,
      account_code: code,
      name,
      account_class: accountClass,
      currency: "GHS",
      balance,
    })),
    counterparties: [
      {
        source_reference: `FILING-${date}-CORPORATE`,
        name: "Synthetic filing customer",
        counterparty_type: "CORPORATE",
        country_code: "GH",
      },
    ],
    products: [
      ["DEP.CORP.CUR", "CORPORATE_DEPOSIT"],
      ["LN.CORP.1Y", "CORPORATE_UNRATED"],
    ].map(([code, category]) => ({
      source_reference: `FILING-${date}-${code}`,
      product_code: code,
      name: code,
      regulatory_category: category,
    })),
    positions: [
      ["DEPOSIT", "1000000", "DEP.CORP.CUR", "2001"],
      ["LOAN", "200000", "LN.CORP.1Y", "1301"],
    ].map(([type, balance, product, gl]) => ({
      source_reference: `FILING-${date}-${type}`,
      position_type: type,
      currency: "GHS",
      balance,
      interest_rate: "0.05",
      rate_type: "FIXED",
      contractual_maturity: type === "LOAN" ? "2099-01-01" : "",
      ifrs9_stage: type === "LOAN" ? "1" : "",
      counterparty_reference: `FILING-${date}-CORPORATE`,
      product_code: product,
      gl_account_code: gl,
    })),
  };
  await writeWorkbook(filename, sheets);
  const base = `/banks/${SAMPLE_BANK_ID}`;
  const mapping = await biApi(request, "admin", `${base}/mapping-configs`, {
    method: "POST",
    data: {
      source_system: "EXCEL_CSV",
      name: `Filing correction ${date}`,
      activate: false,
      reason: "Map the synthetic filing book",
      config: {
        field_mappings: Object.fromEntries(
          Object.entries({
            gl_account: "gl_accounts",
            counterparty: "counterparties",
            product: "products",
            position: "positions",
          }).map(([entity, table]) => [
            entity,
            {
              source_table: table,
              fields: Object.fromEntries(
                Object.keys(sheets[table][0]).map((key) => [key, key]),
              ),
            },
          ]),
        ),
      },
    },
  });
  expect(mapping.status, JSON.stringify(mapping.body)).toBe(200);
  const uploaded = await request.post(
    `${E2E_API_ORIGIN}/api/v1${base}/ingestion-uploads`,
    {
      headers: { Authorization: `Bearer ${await mintBackendToken("admin")}` },
      multipart: {
        file: {
          name: path.basename(filename),
          mimeType:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          buffer: await readFile(filename),
        },
      },
    },
  );
  expect(uploaded.status(), await uploaded.text()).toBe(201);
  const source = IngestionUploadReadFromJSON(await uploaded.json());
  const ingested = await biApi(request, "admin", `${base}/ingestion-batches`, {
    method: "POST",
    data: {
      source_system: "EXCEL_CSV",
      as_of_date: date,
      mapping_config_id: MappingConfigReadFromJSON(mapping.body).id,
      location: source.location,
      reason: "Correct synthetic cash and its balancing share capital",
    },
  });
  expect(ingested.status, JSON.stringify(ingested.body)).toBe(201);
  expect(IngestionBatchStartReadFromJSON(ingested.body).batch).toMatchObject({
    status: "accepted",
    recordsAccepted: 9,
    recordsError: 0,
  });
  const activated = await biApi(request, "admin", `${base}/data-activations`, {
    method: "POST",
    data: {
      as_of_date: date,
      run_calculations: false,
      reason: "Use the corrected filing book",
    },
  });
  expect(activated.status, JSON.stringify(activated.body)).toBe(201);
  const run = await biApi(request, "admin", `${base}/regulatory-runs`, {
    method: "POST",
    data: {
      module: "liquidity",
      reporting_period_id: DataActivationReadFromJSON(activated.body)
        .reportingPeriodId,
      scenario_code: "baseline",
    },
  });
  expect(run.status, JSON.stringify(run.body)).toBe(201);
  const computed = RegulatoryRunReadFromJSON(run.body);
  expect(computed.status).toBe("succeeded");
  expect(Number(computed.metrics.hqla_total_ghs)).toBe(Number(cash));
  return computed.id;
}

export async function withdrawFilingBook(
  request: APIRequestContext,
  date: string,
) {
  const base = `/banks/${SAMPLE_BANK_ID}`;
  for (const entity of ["position", "gl_account", "counterparty", "product"]) {
    const scope = await biApi(
      request,
      "admin",
      `${base}/canonical-withdrawal-scope?entity=${entity}&source_system=EXCEL_CSV&as_of=${date}`,
    );
    expect(scope.status, JSON.stringify(scope.body)).toBe(200);
    if (CanonicalWithdrawalScopeReadFromJSON(scope.body).rowsInScope === 0)
      continue;
    const proposed = await biApi(
      request,
      "admin",
      `${base}/canonical-withdrawals`,
      {
        method: "POST",
        data: {
          entity,
          source_system: "EXCEL_CSV",
          as_of_date: date,
          reason: "Retire this story's synthetic filing book",
          requested_by: E2E_USERS.admin.id,
        },
      },
    );
    expect(proposed.status, JSON.stringify(proposed.body)).toBe(201);
    const approved = await biApi(
      request,
      "approver",
      `${base}/canonical-withdrawals/${CanonicalWithdrawalReadFromJSON(proposed.body).id}/approve`,
      {
        method: "POST",
        data: { approved_by: E2E_USERS.approver.id },
      },
    );
    expect(approved.status, JSON.stringify(approved.body)).toBe(200);
    expect(CanonicalWithdrawalReadFromJSON(approved.body).status).toBe(
      "applied",
    );
  }
}

export function expectFilingCash(
  packageRead: RegulatoryPackageRead,
  cash: number,
) {
  expect(packageRead.snapshot.totals).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        code: "hqla_total_ghs",
        value: expect.any(String),
      }),
    ]),
  );
  const total = packageRead.snapshot.totals.find(
    (row: { code: string }) => row.code === "hqla_total_ghs",
  );
  expect(Number(total.value)).toBe(cash);
}

export async function filingPackage(request: APIRequestContext, id: string) {
  const result = await biApi(request, "admin", packagePath(id));
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  return RegulatoryPackageReadFromJSON(result.body);
}

export async function currentFilingPackage(
  request: APIRequestContext,
  date: string,
) {
  const result = await biApi(
    request,
    "admin",
    `/banks/${SAMPLE_BANK_ID}/regulatory-packages?return_code=LCR-NSFR&reporting_date=${date}`,
  );
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  const packages = RegulatoryPackageListReadFromJSON(result.body).packages;
  expect(packages.length).toBeGreaterThan(0);
  const latest = packages.reduce((left, right) =>
    left.version > right.version ? left : right,
  );
  return filingPackage(request, latest.id);
}

export async function filingChain(
  request: APIRequestContext,
  id: string,
  role = "admin",
) {
  const result = await biApi(request, role, `${packagePath(id)}/workflow`);
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  return PackageChainReadFromJSON(result.body);
}

export async function filingAttestation(
  request: APIRequestContext,
  id: string,
) {
  const result = await biApi(
    request,
    "admin",
    `${packagePath(id)}/attestation`,
  );
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  return AttestationStatusReadFromJSON(result.body);
}

export async function filingEvents(request: APIRequestContext, id: string) {
  const result = await biApi(
    request,
    "admin",
    `${packagePath(id)}/submission-events`,
  );
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  return SubmissionEventListReadFromJSON(result.body);
}

/** Read the immutable signature rows, including withdrawn cycles. */
export function filingSignatures(id: string) {
  const db = new DatabaseSync(path.join(E2E_TMP, "e2e.db"), { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout=5000");
    return db
      .prepare(
        `
      SELECT s.id, s.attestation_cycle, s.package_version, s.signing_role,
             s.signer_id, s.certification_digest, s.content_digest,
             s.snapshot_sha256, s.created_at,
             p.content_digest AS package_content_digest,
             p.snapshot_sha256 AS package_snapshot_sha256
      FROM attestation_signatures s
      JOIN regulatory_packages p ON p.id=s.package_id AND p.organization_id=s.organization_id
      WHERE s.organization_id=? AND s.bank_id=? AND s.package_id=?
      ORDER BY s.created_at, s.id
    `,
      )
      .all(E2E_ORG_ID, SAMPLE_BANK_ID, id.replaceAll("-", ""));
  } finally {
    db.close();
  }
}

/** Wait for the replacement the correction control actually minted. */
export async function generateCorrectedFiling(
  page: Page,
  request: APIRequestContext,
  prior: RegulatoryPackageRead,
  control: Locator,
) {
  const minted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/regulatory-packages$/.test(new URL(response.url()).pathname) &&
      response.ok(),
  );
  await control.click();
  const created = RegulatoryPackageReadFromJSON(await (await minted).json());
  expect(created.id).not.toBe(prior.id);
  expect(created.supersedesId).toBe(prior.id);
  expect(created.version).toBe(prior.version + 1);
  await expect(
    page.getByText(new RegExp(`\\bVersion ${created.version} ·`)),
  ).toBeVisible();
  await expect(page.getByText("Checks passed").first()).toBeVisible();
  await expect(page.getByTestId("attestation-clearance").first()).toHaveText(
    /^Not cleared to submit/i,
  );
  const unsigned = await filingAttestation(request, created.id);
  expect(unsigned).toMatchObject({
    attestationState: "unsigned",
    canSubmit: false,
    signatures: [],
  });
  const events = await filingEvents(request, created.id);
  const refused = await biApi(
    request,
    "validator",
    `${packagePath(created.id)}/submit`,
    { method: "POST", data: { channel: "orass_sandbox" } },
  );
  expect(refused.status, JSON.stringify(refused.body)).toBe(409);
  expect(await filingEvents(request, created.id)).toEqual(events);
  return filingPackage(request, created.id);
}

/** A real previous version's review basis must not approve its replacement. */
export async function expectStaleReviewRefused(
  request: APIRequestContext,
  id: string,
  stale: PackageChainRead,
) {
  const before = await filingChain(request, id, "approver");
  expect(before.reviewDigest).not.toBe(stale.reviewDigest);
  const signatures = filingSignatures(id);
  const refused = await biApi(
    request,
    "approver",
    `${packagePath(id)}/workflow/decisions`,
    {
      method: "POST",
      data: {
        decision: "approved",
        round: before.round,
        review_digest: stale.reviewDigest,
      },
    },
  );
  expect(refused.status, JSON.stringify(refused.body)).toBe(409);
  expect(refused.body).toMatchObject({
    error: {
      code: "http_error",
      details: {
        error_code: "review_basis_changed",
        expected: before.reviewDigest,
      },
    },
  });
  expect(await filingChain(request, id, "approver")).toEqual(before);
  expect(filingSignatures(id)).toEqual(signatures);
}

export async function expectFreshFilingSignatures(
  request: APIRequestContext,
  id: string,
  previous: ReturnType<typeof filingSignatures>,
) {
  const attestation = await filingAttestation(request, id);
  expect(attestation).toMatchObject({
    attestationState: "fully_certified",
    canSubmit: true,
  });
  expect(
    attestation.signatures.map((signature) => signature.signingRole).sort(),
  ).toEqual(["approver", "preparer"]);
  expect(
    new Set(attestation.signatures.map((signature) => signature.signerId)).size,
  ).toBe(2);
  const oldIds = previous.map((row) => row.id);
  for (const signature of attestation.signatures) {
    expect(oldIds).not.toContain(signature.id.replaceAll("-", ""));
    expect(signature.certificationDigest).toBe(attestation.certificationDigest);
    expect(signature.attestationCycle).toBe(attestation.attestationCycle);
  }
  const signatures = filingSignatures(id);
  expect(signatures).toHaveLength(2);
  for (const signature of signatures) {
    expect(signature.content_digest).toBe(signature.package_content_digest);
    expect(signature.snapshot_sha256).toBe(signature.package_snapshot_sha256);
  }
}

/** A duplicate Validator approval is idempotent; a duplicate transmission is refused. */
export async function expectRepeatedApprovalSafe(
  request: APIRequestContext,
  id: string,
) {
  const before = await filingChain(request, id, "validator");
  expect(before.awaitingHandOff).toBe(true);
  const replay = await biApi(
    request,
    "validator",
    `${packagePath(id)}/workflow/decisions`,
    {
      method: "POST",
      data: {
        decision: "approved",
        round: before.round,
        review_digest: before.reviewDigest,
      },
    },
  );
  expect(replay.status, JSON.stringify(replay.body)).toBe(200);
  expect(PackageChainReadFromJSON(replay.body)).toEqual(before);
}

export async function expectRepeatedSubmissionRefused(
  request: APIRequestContext,
  id: string,
) {
  const before = await filingEvents(request, id);
  const replay = await biApi(
    request,
    "validator",
    `${packagePath(id)}/submit`,
    { method: "POST", data: { channel: "orass_sandbox" } },
  );
  expect(replay.status, JSON.stringify(replay.body)).toBe(409);
  expect(await filingEvents(request, id)).toEqual(before);
}

export async function expectPriorFilingFrozen(
  request: APIRequestContext,
  prior: RegulatoryPackageRead,
  signatures: ReturnType<typeof filingSignatures>,
) {
  const after = await filingPackage(request, prior.id);
  expect(after.status).toBe("superseded");
  expect(after.snapshot).toEqual(prior.snapshot);
  expect(after.sourceRuns).toEqual(prior.sourceRuns);
  expect(after.snapshotSha256).toBe(prior.snapshotSha256);
  expect(after.regulatorComments).toBe(prior.regulatorComments);
  expect(filingSignatures(prior.id)).toEqual(signatures);
}
