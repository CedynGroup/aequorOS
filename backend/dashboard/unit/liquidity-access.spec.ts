import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { hrefAccess, isHrefVisible, type ModuleScope } from "../lib/modules";

const routes = [
  "/liquidity",
  "/liquidity/buffer",
  "/liquidity/nsfr",
  "/liquidity/forecast",
  "/liquidity/monitoring",
  "/liquidity/cfp",
  "/liquidity/stress",
] as const;

function scope(overrides: Partial<ModuleScope> = {}): ModuleScope {
  return {
    modules: new Set(["liquidity"]),
    entitledModules: new Set(["liquidity"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    liquidityAggregatedView: false,
    liquidityConfidentialView: false,
    riskConfidentialView: false,
    ...overrides,
  };
}

describe("liquidity navigation grants", () => {
  it("explains the exact missing grant instead of granting confidential access from an aggregate grant", () => {
    const aggregateOnly = scope({ liquidityAggregatedView: true });
    expect(hrefAccess("/liquidity", aggregateOnly).state).toBe("enabled");
    const denied = hrefAccess("/liquidity/cfp", aggregateOnly);
    expect(denied.state).toBe("disabled");
    if (denied.state === "disabled")
      expect(denied.reason).toMatch(/Liquidity.*Confidential.*View/i);
  });

  it("enforces the institution and sensitivity matrix regardless of query strings and trailing slashes", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...routes),
        fc.constantFrom("bank", "sdi"),
        fc.boolean(),
        fc.boolean(),
        fc.boolean(),
        fc.constantFrom("", "/", "?period=fixture", "/?period=fixture"),
        (route, institutionClass, aggregated, confidential, risk, suffix) => {
          const actual = hrefAccess(
            route + suffix,
            scope({
              institutionClass,
              liquidityAggregatedView: aggregated,
              liquidityConfidentialView: confidential,
              riskConfidentialView: risk,
            }),
          );
          // Independent policy table: SDIs have no Basel buffer/NSFR; stress couples LIQ and RISK.
          const hidden =
            institutionClass === "sdi" &&
            ["/liquidity/buffer", "/liquidity/nsfr"].includes(route);
          const needsConfidential =
            [
              "/liquidity/forecast",
              "/liquidity/monitoring",
              "/liquidity/cfp",
              "/liquidity/stress",
            ].includes(route) ||
            (institutionClass === "sdi" && route === "/liquidity");
          const allowed =
            (needsConfidential ? confidential : aggregated) &&
            (route !== "/liquidity/stress" || risk);
          expect(actual.state).toBe(
            hidden ? "hidden" : allowed ? "enabled" : "disabled",
          );
          expect(
            isHrefVisible(
              route + suffix,
              scope({
                institutionClass,
                liquidityAggregatedView: aggregated,
                liquidityConfidentialView: confidential,
                riskConfidentialView: risk,
              }),
            ),
          ).toBe(!hidden && allowed);
        },
      ),
      { numRuns: 100 },
    );
  });

  it("does not expose liquidity before scope resolves or when the institution is not entitled", () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...routes),
        fc.boolean(),
        fc.boolean(),
        (route, aggregated, confidential) => {
          const grants = {
            liquidityAggregatedView: aggregated,
            liquidityConfidentialView: confidential,
            riskConfidentialView: true,
          };
          expect(
            hrefAccess(route, scope({ ...grants, isResolved: false })).state,
          ).toBe("hidden");
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
        },
      ),
      { numRuns: 100 },
    );
  });
});
