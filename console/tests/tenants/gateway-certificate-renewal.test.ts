/**
 * Node tests for the console's gateway certificate renewal panel.
 *
 * What is being protected: a bank whose gateway client certificate lapses can no
 * longer file. Staff must see when a certificate is due, whether the staff plane's
 * scheduled check is actually running for the bank, and where an unfinished
 * renewal stopped; and the actions they are offered must match what the staff
 * API accepts (resume an unfinished renewal; cancel only before the switch).
 *
 * Two halves, as for the other tenant surfaces: the pure half pins every
 * decision the panel shows, and the surface half reads the SOURCE to assert the
 * panel is mounted on the tenant's gateway card and posts to the staff API's own
 * paths.
 *
 * Run by `pnpm --filter @aequoros/console test`.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import type {
  GatewayCertificateRenewal,
  ReportingGatewayEnrolment,
} from "../../lib/api";
import {
  certificateState,
  isCancellable,
  isUnfinished,
  scheduledCheckRunning,
} from "../../lib/gateway-renewal";

// Compiled to console/.test-out/tests/tenants, so the console root is three up.
const CONSOLE_ROOT = join(__dirname, "..", "..", "..");
const NOW = Date.parse("2026-11-22T12:00:00Z");

function enrolment(
  overrides: Partial<ReportingGatewayEnrolment> = {},
): ReportingGatewayEnrolment {
  return {
    bank_id: "BK-SAMP0001",
    enrolled: true,
    institution_code: "0160",
    environment: "production",
    client_id: "sbl-prod-01",
    certificate_not_after: "2027-11-22T12:00:00Z",
    signing_kid: "0160-sign-20261122",
    enrolled_at: "2026-11-22T09:00:00Z",
    last_connection_test_at: "2026-11-22T09:00:00Z",
    last_connection_test_ok: true,
    renewal_due_from: "2027-10-23T12:00:00Z",
    last_renewal_check_at: null,
    certificate_renewal: null,
    ...overrides,
  };
}

function renewal(
  overrides: Partial<GatewayCertificateRenewal> = {},
): GatewayCertificateRenewal {
  return {
    status: "complete",
    stage: "previous_revoked",
    trigger: "scheduled",
    requested_by: "AequorOS scheduled renewal",
    reason: "The certificate expires on 22 Nov 2027.",
    started_at: "2027-10-23T12:00:00Z",
    updated_at: "2027-10-23T12:00:05Z",
    completed_at: "2027-10-23T12:00:05Z",
    previous_client_id: "sbl-prod-01",
    previous_not_after: "2027-11-22T12:00:00Z",
    new_client_id: "sbl-prod-02",
    previous_revoked_at: "2027-10-23T12:00:05Z",
    error: null,
    failures: 0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// The certificate's standing
// ---------------------------------------------------------------------------

test("a certificate with a year to run is valid", () => {
  assert.deepEqual(certificateState(enrolment(), NOW), {
    label: "Valid",
    tone: "ok",
  });
});

test("inside the renewal window the certificate is due", () => {
  const state = certificateState(
    enrolment({ renewal_due_from: "2026-11-01T00:00:00Z" }),
    NOW,
  );
  assert.deepEqual(state, { label: "Due for renewal", tone: "warn" });
});

test("an expired certificate says so before anything else", () => {
  const state = certificateState(
    enrolment({
      certificate_not_after: "2026-11-21T00:00:00Z",
      certificate_renewal: renewal({
        status: "failed",
        stage: "client_issued",
      }),
    }),
    NOW,
  );
  assert.deepEqual(state, { label: "Expired", tone: "crit" });
});

test("an unfinished renewal names where it stopped", () => {
  const failed = enrolment({
    certificate_renewal: renewal({ status: "failed", stage: "key_prepared" }),
  });
  assert.equal(certificateState(failed, NOW).label, "Renewal failed");
  const pending = enrolment({
    certificate_renewal: renewal({
      status: "revocation_pending",
      stage: "switched",
    }),
  });
  assert.equal(
    certificateState(pending, NOW).label,
    "Old client not yet revoked",
  );
});

// ---------------------------------------------------------------------------
// The actions offered match what the staff API accepts
// ---------------------------------------------------------------------------

test("an unfinished renewal is resumed, a finished one is started afresh", () => {
  assert.equal(
    isUnfinished(renewal({ status: "failed", stage: "client_issued" })),
    true,
  );
  assert.equal(
    isUnfinished(renewal({ status: "revocation_pending", stage: "switched" })),
    true,
  );
  assert.equal(isUnfinished(renewal()), false);
  assert.equal(
    isUnfinished(renewal({ status: "cancelled", stage: "client_issued" })),
    false,
  );
  assert.equal(isUnfinished(null), false);
});

test("only a renewal that has not switched the bank over can be cancelled", () => {
  assert.equal(
    isCancellable(renewal({ status: "failed", stage: "key_prepared" })),
    true,
  );
  assert.equal(
    isCancellable(renewal({ status: "failed", stage: "client_issued" })),
    true,
  );
  assert.equal(
    isCancellable(renewal({ status: "revocation_pending", stage: "switched" })),
    false,
  );
  assert.equal(isCancellable(renewal()), false);
  assert.equal(isCancellable(null), false);
});

test("the scheduled check counts as running only on recent evidence", () => {
  assert.equal(scheduledCheckRunning(enrolment(), NOW), false);
  assert.equal(
    scheduledCheckRunning(
      enrolment({ last_renewal_check_at: "2026-11-22T06:00:00Z" }),
      NOW,
    ),
    true,
  );
  assert.equal(
    scheduledCheckRunning(
      enrolment({ last_renewal_check_at: "2026-11-18T06:00:00Z" }),
      NOW,
    ),
    false,
  );
});

// ---------------------------------------------------------------------------
// The surface is wired
// ---------------------------------------------------------------------------

test("the client posts to the staff API renewal paths", () => {
  const source = readFileSync(join(CONSOLE_ROOT, "lib", "api.ts"), "utf8");
  assert.match(
    source,
    /\$\{gatewayPath\(orgId, bankId\)\}\/certificate-renewal`/,
  );
  assert.match(
    source,
    /\$\{gatewayPath\(orgId, bankId\)\}\/certificate-renewal\/cancel`/,
  );
});

test("the panel is mounted on the tenant's gateway card for an enrolled bank", () => {
  const section = readFileSync(
    join(CONSOLE_ROOT, "components", "tenants", "ReportingGatewaySection.tsx"),
    "utf8",
  );
  assert.match(section, /enrolment\?\.enrolled && \(/);
  assert.match(section, /<GatewayCertificateRenewal/);
  const panel = readFileSync(
    join(
      CONSOLE_ROOT,
      "components",
      "tenants",
      "GatewayCertificateRenewal.tsx",
    ),
    "utf8",
  );
  assert.match(panel, /renewReportingGatewayCertificate\(/);
  assert.match(panel, /cancelReportingGatewayCertificateRenewal\(/);
  // A reason is required for every renewal and cancellation.
  assert.match(panel, /disabled=\{!reasonValid \|\| busy !== null\}/);
});
