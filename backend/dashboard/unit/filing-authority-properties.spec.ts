import { expect, it } from "vitest";
import fc from "fast-check";
import type {
  EffectiveAuthorityRead,
  EffectiveCapabilityRead,
} from "@aequoros/risk-service-api";
import { filingAuthorityFor } from "../lib/submissions/filingAuthority";

const capability: fc.Arbitrary<EffectiveCapabilityRead> = fc.record({
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
  requiresContextualAuthorization: fc.boolean(),
  dataScope: fc.constant({ kind: "all" as const, branches: [], regions: [] }),
});

it("offers filing acts only from the selected institution's exact regulatory capabilities", () => {
  fc.assert(
    fc.property(
      fc.array(capability, { maxLength: 25 }),
      fc.array(capability, { maxLength: 25 }),
      fc.boolean(),
      fc.boolean(),
      (selected, sibling, resolved, readOnly) => {
        const projection: EffectiveAuthorityRead = {
          authv: 1,
          // Organization authority is not authority over the bank on screen.
          organizationCapabilities: sibling,
          institutionCapabilities: [
            { institutionId: "BK-A", capabilities: selected },
            { institutionId: "BK-B", capabilities: sibling },
          ],
        };
        const actual = filingAuthorityFor(projection, "BK-A", {
          resolved,
          readOnly,
        });
        const gates = [
          ["mayPrepare", "create", false],
          ["mayRunChecks", "validate", false],
          ["mayExport", "export", false],
          ["mayApprove", "approve", true],
          ["mayTransmit", "submit", true],
        ] as const;
        for (const [act, permission, restricted] of gates) {
          expect(actual[act]).toBe(
            resolved &&
              !readOnly &&
              selected.some(
                (entry) =>
                  entry.module === "reg" &&
                  entry.permission === permission &&
                  (!restricted || entry.sensitivity === "restricted"),
              ),
          );
          expect(
            filingAuthorityFor(projection, "BK-C", { resolved, readOnly })[act],
          ).toBe(false);
          expect(
            filingAuthorityFor(undefined, "BK-A", { resolved, readOnly })[act],
          ).toBe(false);
        }
      },
    ),
    { numRuns: 100 },
  );
});
