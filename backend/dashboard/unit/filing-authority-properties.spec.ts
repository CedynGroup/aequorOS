import { expect, it } from "vitest";
import fc from "fast-check";
import type { EffectiveAuthorityRead } from "@aequoros/risk-service-api";
import { capabilitySetArbitrary } from "./submissions/testing/generators";
import { assertFilingAuthorityUnchanged } from "./submissions/testing/assertions";
import { filingAuthorityFor } from "../lib/submissions/filingAuthority";

it("offers filing acts only from the selected institution's exact regulatory capabilities", () => {
  fc.assert(
    fc.property(
      capabilitySetArbitrary,
      capabilitySetArbitrary,
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
        assertFilingAuthorityUnchanged(
          filingAuthorityFor(
            {
              ...projection,
              institutionCapabilities: [
                { institutionId: "BK-B", capabilities: [] },
                {
                  institutionId: "BK-A",
                  capabilities: [...selected].reverse(),
                },
              ],
            },
            "BK-A",
            { resolved, readOnly },
          ),
          actual,
        );
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
