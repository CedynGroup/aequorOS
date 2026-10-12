import { expect, it } from "vitest";
import fc from "fast-check";
import {
  capabilitySetArbitrary,
  filingHistoryArbitrary,
  filingTransitions,
  officerArbitrary,
  officerProjection,
  transmitStepArbitrary,
  transmitSteps,
} from "./submissions/testing/generators";
import { assertFilingAuthorityUnchanged } from "./submissions/testing/assertions";
import { filingAuthorityFor } from "../lib/submissions/filingAuthority";

it("generates contiguous filing histories with terminal states at the end", () => {
  fc.assert(
    fc.property(filingHistoryArbitrary, (history) => {
      expect(history[0]).toBe("draft");
      for (let i = 1; i < history.length; i++) {
        expect(filingTransitions[history[i - 1]!]).toContain(history[i]);
      }
    }),
    { numRuns: 100 },
  );
});

it("generates unique, scoped capabilities with consistent contextual flags", () => {
  fc.assert(
    fc.property(capabilitySetArbitrary, (capabilities) => {
      expect(
        new Set(
          capabilities.map(
            (row) => `${row.module}:${row.sensitivity}:${row.permission}`,
          ),
        ).size,
      ).toBe(capabilities.length);
      for (const row of capabilities) {
        expect(row.requiresContextualAuthorization).toBe(
          row.permission === "approve" || row.permission === "submit",
        );
        expect(row.dataScope).toEqual({
          kind: "all",
          branches: [],
          regions: [],
        });
      }
    }),
    { numRuns: 100 },
  );
});

it("generates real officer hand-offs and dialog steps", () => {
  fc.assert(
    fc.property(officerArbitrary, transmitStepArbitrary, (officer, [step]) => {
      const projection = officerProjection(officer);
      const actual = filingAuthorityFor(projection, "BK-A", { resolved: true });
      expect(actual.mayPrepare).toBe(officer === "preparer");
      expect(actual.mayApprove).toBe(officer === "approver");
      expect(actual.mayTransmit).toBe(officer === "validator");
      expect(transmitSteps).toContain(step);
      const sibling = filingAuthorityFor(projection, "BK-B", {
        resolved: true,
      });
      expect(
        sibling.mayPrepare || sibling.mayApprove || sibling.mayTransmit,
      ).toBe(false);
      assertFilingAuthorityUnchanged(
        filingAuthorityFor(structuredClone(projection), "BK-A", {
          resolved: true,
        }),
        actual,
      );
    }),
    { numRuns: 100 },
  );
});
