/** Synthetic filing responses; owned by the submissions feature tests. */
import fc from "fast-check";
import type {
  EffectiveAuthorityRead,
  EffectiveCapabilityRead,
  PackageStatus,
} from "@aequoros/risk-service-api";

// Sources: backend/docs/state_machine_testing.md, filing PackageStatus contract.
// Server checkpoints are independent of the UI's action/transition helpers.
export const filingTransitions: Record<
  PackageStatus,
  readonly PackageStatus[]
> = {
  draft: ["generated"],
  generated: ["validated", "superseded"],
  validated: ["pending_approval", "superseded"],
  pending_approval: ["approved", "rejected"],
  approved: ["submitted", "superseded"],
  submitted: ["acknowledged", "rejected", "declined"],
  rejected: ["superseded"],
  acknowledged: [],
  declined: [],
  superseded: [],
};
export const filingStatuses = Object.keys(filingTransitions) as PackageStatus[];
export const officers = [
  "preparer",
  "approver",
  "validator",
  "reader",
] as const;
export type Officer = (typeof officers)[number];
export const officerArbitrary = fc.constantFrom(...officers);
export const filingStatusArbitrary = fc.constantFrom(...filingStatuses);

/** Role hand-offs: backend/app/reporting/domain/workflow.py; no scalar token roles. */
export function officerProjection(officer: Officer): EffectiveAuthorityRead {
  const permissions = {
    preparer: ["create", "validate", "export"],
    approver: ["approve"],
    validator: ["submit"],
    reader: [],
  } as const;
  return {
    authv: 1,
    organizationCapabilities: [],
    institutionCapabilities: [
      {
        institutionId: "BK-A",
        capabilities: permissions[officer].map((permission) => ({
          module: "reg",
          sensitivity: "restricted",
          permission,
          dataScope: { kind: "all", branches: [], regions: [] },
          requiresContextualAuthorization:
            permission === "approve" || permission === "submit",
        })),
      },
    ],
  };
}

/** EffectiveCapabilityRead units are categorical; boundary rows use wrong module/sensitivity.
 * Source: backend/app/core/authorization capability projection and generated API contract.
 * Workflow permissions require a contextual check; it is never sampled inconsistently.
 */
export const capabilityArbitrary: fc.Arbitrary<EffectiveCapabilityRead> = fc
  .record({
    module: fc.constantFrom("reg" as const, "credit" as const, "fx" as const),
    sensitivity: fc.constantFrom(
      "published" as const,
      "aggregated" as const,
      "confidential" as const,
      "restricted" as const,
    ),
    permission: fc.constantFrom(
      "view" as const,
      "create" as const,
      "validate" as const,
      "export" as const,
      "approve" as const,
      "submit" as const,
    ),
  })
  .map((row) => ({
    ...row,
    requiresContextualAuthorization:
      row.permission === "approve" || row.permission === "submit",
    dataScope: { kind: "all", branches: [], regions: [] },
  }));

export const capabilitySetArbitrary = fc.uniqueArray(capabilityArbitrary, {
  maxLength: 25,
  selector: (row) => `${row.module}:${row.sensitivity}:${row.permission}`,
});

export const transmitSteps = [
  "acknowledge",
  "pending",
  "refused",
  "confirm",
  "cancelAndReopen",
] as const;
export type TransmitStep = (typeof transmitSteps)[number];
/** Public dialog inputs: components/submissions/TransmitConfirmDialog.tsx. */
export const transmitStepArbitrary = fc.tuple(
  fc.constantFrom(...transmitSteps),
  fc.boolean(),
);

/** Valid response histories, capped for CI. No bank data or production identifiers. */
export const filingHistoryArbitrary = fc
  .array(fc.nat(), { maxLength: 9 })
  .map((choices) => {
    const history: PackageStatus[] = ["draft"];
    for (const choice of choices) {
      const next = filingTransitions[history.at(-1)!];
      if (!next.length) break;
      history.push(next[choice % next.length]!);
    }
    return history;
  });
