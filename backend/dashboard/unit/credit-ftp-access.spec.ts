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
    modules: new Set(["credit", "ftp"]),
    entitledModules: new Set(["credit", "ftp"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    ...overrides,
  };
}
function grant(
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  kind: EffectiveCapabilityRead["dataScope"]["kind"] = "all",
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module: "credit",
    sensitivity,
    permission: "view",
    requiresContextualAuthorization: false,
    dataScope: {
      kind,
      branches: kind === "branch" || kind === "mixed" ? ["fixture-branch"] : [],
      regions: kind === "region" || kind === "mixed" ? ["fixture-region"] : [],
    },
    ...overrides,
  };
}
const creditRoutes = [
  "/credit",
  "/credit/book",
  "/credit/concentration",
  "/credit/activity",
  "/credit/delinquency",
  "/credit/vintages",
] as const;
const ftpRoutes = [
  "/ftp",
  "/ftp/lines",
  "/ftp/products",
  "/ftp/rules",
  "/ftp/expost",
  "/ftp/scenarios",
] as const;
const suffix = fc.constantFrom("", "/", "?period=fixture", "/?period=fixture");

describe("credit and FTP grant boundaries", () => {
  it("allows scoped credit rows but requires whole-institution grants for aggregate figures", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...creditRoutes),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        fc.constantFrom("all", "branch", "region", "mixed"),
        suffix,
        (route, aggregated, restricted, confidential, kind, ending) => {
          const actualScope = scope({
            institutionCapabilities: [
              ...(aggregated ? [grant("aggregated")] : []),
              ...(restricted ? [grant("restricted", kind)] : []),
              ...(confidential ? [grant("confidential", kind)] : []),
            ],
          });
          const allowed =
            route === "/credit/book"
              ? restricted
              : route === "/credit/concentration"
                ? aggregated && restricted && kind === "all"
                : route === "/credit/activity"
                  ? aggregated && confidential
                  : aggregated;
          expect(hrefAccess(route + ending, actualScope).state).toBe(
            allowed ? "enabled" : "hidden",
          );
          expect(isHrefVisible(route + ending, actualScope)).toBe(allowed);
          if (allowed)
            expect(accessDeniedForPath(route + ending, actualScope)).toBeNull();
          else
            expect(
              accessDeniedForPath(route + ending, actualScope)?.requirements
                .length,
            ).toBeGreaterThan(0);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("refuses credit reads supported only by contextual, wrong-module or mutation grants", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...creditRoutes),
        fc.constantFrom<Partial<EffectiveCapabilityRead>>(
          { permission: "run" },
          { permission: "approve" },
          { module: "ftp" },
          { requiresContextualAuthorization: true },
        ),
        (route, wrong) => {
          const actualScope = scope({
            institutionCapabilities: [
              grant("aggregated", "all", wrong),
              grant("confidential", "all", wrong),
              grant("restricted", "all", wrong),
            ],
          });
          expect(hrefAccess(route, actualScope).state).toBe("hidden");
          expect(isHrefVisible(route, actualScope)).toBe(false);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("requires confidential FTP scenario view independently of aggregate and run authority", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...ftpRoutes),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        suffix,
        (route, aggregated, confidential, run, ending) => {
          const actualScope = scope({
            ftpAggregatedView: aggregated,
            ftpConfidentialView: confidential,
            ftpRun: run,
          });
          const allowed =
            route === "/ftp/scenarios" ? confidential : aggregated;
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
                "ftp",
                route === "/ftp/scenarios" ? "confidential" : "aggregated",
                "view",
              ],
            ]);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("does not enable unentitled or unauthorized institutions despite otherwise sufficient grants", () => {
    fc.assert(
      fc.property(fc.constantFrom(...creditRoutes, ...ftpRoutes), (route) => {
        const grants = {
          institutionCapabilities: [
            grant("aggregated"),
            grant("confidential"),
            grant("restricted"),
          ],
          ftpAggregatedView: true,
          ftpConfidentialView: true,
          ftpRun: true,
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
