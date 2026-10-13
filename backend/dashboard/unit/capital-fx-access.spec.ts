import { describe, expect, it } from "vitest";
import fc from "fast-check";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import {
  accessDeniedForPath,
  hrefAccess,
  isHrefVisible,
  type ModuleScope,
} from "../lib/modules";

function scope(overrides: Partial<ModuleScope> = {}): ModuleScope {
  return {
    modules: new Set(["capital", "fx"]),
    entitledModules: new Set(["capital", "fx"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    ...overrides,
  };
}
function capitalGrant(
  sensitivity: "aggregated" | "confidential",
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module: "cap",
    sensitivity,
    permission: "view",
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
    ...overrides,
  };
}
const capitalRoutes = [
  "/basel",
  "/basel/rwa",
  "/basel/structure",
  "/basel/stress",
  "/basel/planning",
] as const;
const fxRoutes = [
  "/fx",
  "/fx/forwards",
  "/fx/hedges",
  "/fx/limits",
  "/fx/scenarios",
  "/fx/var",
] as const;
const suffix = fc.constantFrom("", "/", "?period=fixture", "/?period=fixture");

describe("capital and FX route authority", () => {
  it("requires the exact whole-institution capital sensitivity and respects the SDI regime", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...capitalRoutes),
        fc.constantFrom("bank", "sdi"),
        fc.boolean(),
        fc.boolean(),
        suffix,
        (route, institutionClass, aggregated, confidential, ending) => {
          const actualScope = scope({
            institutionClass,
            institutionCapabilities: [
              ...(aggregated ? [capitalGrant("aggregated")] : []),
              ...(confidential ? [capitalGrant("confidential")] : []),
            ],
          });
          const hidden = institutionClass === "sdi" && route !== "/basel";
          const allowed =
            route === "/basel/planning" ? confidential : aggregated;
          const actual = hrefAccess(route + ending, actualScope);
          expect(actual.state).toBe(hidden || !allowed ? "hidden" : "enabled");
          expect(isHrefVisible(route + ending, actualScope)).toBe(
            !hidden && allowed,
          );
          const denied = accessDeniedForPath(route + ending, actualScope);
          if (!hidden && !allowed)
            expect(
              denied?.requirements.map((item) => [
                item.moduleScope,
                item.sensitivityScope,
                item.permission,
              ]),
            ).toEqual([
              [
                "cap",
                route === "/basel/planning" ? "confidential" : "aggregated",
                "view",
              ],
            ]);
          else expect(denied).toBeNull();
        },
      ),
      { numRuns: 100 },
    );
  });

  it("does not open capital reads with partial, contextual, wrong-module or mutation-only grants", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...capitalRoutes),
        fc.constantFrom<Partial<EffectiveCapabilityRead>>(
          {
            dataScope: {
              kind: "branch",
              branches: ["fixture-branch"],
              regions: [],
            },
          },
          {
            dataScope: {
              kind: "region",
              branches: [],
              regions: ["fixture-region"],
            },
          },
          { requiresContextualAuthorization: true },
          { module: "fx" },
          { permission: "run" },
          { permission: "approve" },
        ),
        (route, wrongGrant) => {
          const actualScope = scope({
            capitalAggregatedView: true,
            capitalConfidentialView: true,
            capitalRun: true,
            institutionCapabilities: [
              capitalGrant("aggregated", wrongGrant),
              capitalGrant("confidential", wrongGrant),
            ],
          });
          expect(hrefAccess(route, actualScope).state).toBe("hidden");
          expect(isHrefVisible(route, actualScope)).toBe(false);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("keeps FX scenario reads confidential without upgrading aggregate or run authority", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...fxRoutes),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        suffix,
        (route, aggregated, confidential, run, ending) => {
          const actualScope = scope({
            fxAggregatedView: aggregated,
            fxConfidentialView: confidential,
            fxRun: run,
          });
          const allowed = route === "/fx/scenarios" ? confidential : aggregated;
          expect(hrefAccess(route + ending, actualScope).state).toBe(
            allowed ? "enabled" : "disabled",
          );
          expect(isHrefVisible(route + ending, actualScope)).toBe(allowed);
          if (!allowed)
            expect(
              accessDeniedForPath(
                route + ending,
                actualScope,
              )?.requirements.map((item) => [
                item.moduleScope,
                item.sensitivityScope,
                item.permission,
              ]),
            ).toEqual([
              [
                "fx",
                route === "/fx/scenarios" ? "confidential" : "aggregated",
                "view",
              ],
            ]);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("does not enable unentitled institutions even when grants are otherwise sufficient", () => {
    fc.assert(
      fc.property(fc.constantFrom(...capitalRoutes, ...fxRoutes), (route) => {
        const grants = {
          institutionCapabilities: [
            capitalGrant("aggregated"),
            capitalGrant("confidential"),
          ],
          fxAggregatedView: true,
          fxConfidentialView: true,
        };
        expect(
          hrefAccess(route, scope({ ...grants, entitledModules: new Set() }))
            .state,
        ).toBe("hidden");
        expect(
          hrefAccess(
            route,
            scope({ ...grants, hasInstitutionAuthority: false }),
          ).state,
        ).not.toBe("enabled");
      }),
      { numRuns: 100 },
    );
  });
});
