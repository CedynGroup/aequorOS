import {
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";
import {
  AttestationStatusReadFromJSON,
  PackageChainReadFromJSON,
  RegulatoryPackageListReadFromJSON,
  RegulatoryPackageReadFromJSON,
  SubmissionEventListReadFromJSON,
  type PackageChainRead,
  type RegulatoryPackageRead,
} from "@aequoros/risk-service-api";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { E2E_TMP } from "../../playwright.config";
import { biApi, SAMPLE_BANK_ID } from "./bi";
import { E2E_ORG_ID } from "./mint";

const packagePath = (id: string) =>
  `/banks/${SAMPLE_BANK_ID}/regulatory-packages/${id}`;

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
