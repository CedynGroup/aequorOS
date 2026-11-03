// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import fc from "fast-check";
import type {
  EffectiveCapabilityRead,
  EffectiveAuthorityRead,
} from "@aequoros/risk-service-api";
import ProfileProvider, {
  useUserProfile,
} from "../components/profile/ProfileProvider";
import ScenarioLibrary from "../components/stress/ScenarioLibrary";
import ScenarioBuilder from "../components/stress/ScenarioBuilder";
import type {
  MacroScenario,
  MacroScenarioSummary,
} from "../components/stress/types";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import {
  hasAccountAdministrationAuthority,
  hasAccountDirectoryAuthority,
  hasGrantAdministrationAuthority,
} from "../lib/api/accountAdministration";
import { effectiveOrganizationModules } from "../lib/modules";

const bankId = "BK-SAMP0001";
type Permission = EffectiveCapabilityRead["permission"];
const permissions = {
  viewer: ["view"],
  analyst: ["view", "create", "edit", "run", "validate", "export"],
  approver: ["view", "review", "approve"],
} satisfies Record<string, Permission[]>;
type Bundle = keyof typeof permissions;
const server = setupServer();
let client: QueryClient;
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client?.clear();
  server.resetHandlers();
  setAccessToken(null);
});

function summary(
  id: string,
  status: MacroScenarioSummary["status"],
  owner: MacroScenarioSummary["owner"] = "organization",
): MacroScenarioSummary {
  return {
    id,
    name: id,
    code: id,
    bank_id: null,
    scenario_type: "adverse",
    severity: "severe",
    horizon_years: 3,
    status,
    version: 1,
    path_count: 3,
    created_by: "maker",
    approved_by: null,
    owner,
    is_runnable: true,
    is_immutable: owner === "system",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  };
}
const draft: MacroScenario = {
  ...summary("organization_draft", "draft"),
  organization_id: "org",
  description: null,
  narrative: null,
  source: null,
  approval_timestamp: null,
  institution_type_applicability: null,
  paths: [
    {
      variable: "gdp_growth",
      year_index: 1,
      base_value: "0.04",
      stress_value: "-0.03",
    },
  ],
};

function Ready({ children }: { children: ReactNode }) {
  const profile = useUserProfile();
  return profile.isLoading ? null : children;
}
function mount(children: ReactNode, bundle: Bundle, organization: boolean) {
  const capabilities = permissions[bundle].map((permission) => ({
    module: "risk",
    sensitivity: "confidential",
    permission,
    requires_contextual_authorization: permission === "approve",
    data_scope: { kind: "all", branches: [], regions: [] },
  }));
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json({
        effective_authority: {
          authv: 1,
          can_administer_grants: false,
          organization_capabilities: organization ? capabilities : [],
          institution_capabilities: [{ institution_id: bankId, capabilities }],
        },
      }),
    ),
    http.get(`${apiBaseUrl}/macro-scenarios`, () =>
      HttpResponse.json({
        scenarios: [
          summary("organization_draft", "draft"),
          summary("organization_pending", "pending_approval"),
          summary("system_scenario", "approved", "system"),
        ],
        total: 3,
      }),
    ),
  );
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("fixture");
  return render(
    <SessionProvider
      session={{
        expires: "2099-01-01T00:00:00Z",
        accessToken: "fixture",
        user: {
          email: "fixture@example.test",
          organizationId: "org",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={{ tenantId: "org", authorityId: "fixture|authv:1" }}
        >
          <ProfileProvider>
            <Ready>{children}</Ready>
          </ProfileProvider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>,
  );
}
const cases = (["viewer", "analyst", "approver"] as const).flatMap((bundle) =>
  [true, false].map((organization) => ({ bundle, organization })),
);
it.each(cases)(
  "gates organization scenario actions for $bundle (organization grant: $organization)",
  async ({ bundle, organization }) => {
    mount(
      <ScenarioLibrary
        bankId={bankId}
        selectedIds={[]}
        onToggleSelect={() => {}}
        onEdit={() => {}}
        onNew={() => {}}
      />,
      bundle,
      organization,
    );
    await screen.findByText("organization_draft", { selector: "span" });
    const row = (name: string) =>
      within(screen.getByText(name, { selector: "span" }).closest("li")!);
    const controls = [
      [
        row("system_scenario"),
        "Clone to editable draft",
        "create",
        organization && bundle === "analyst",
      ],
      [
        row("organization_draft"),
        "Edit draft",
        "edit",
        organization && bundle === "analyst",
      ],
      [
        row("organization_draft"),
        "Submit for approval",
        "edit",
        organization && bundle === "analyst",
      ],
      [
        row("organization_draft"),
        "Archive",
        "edit",
        organization && bundle === "analyst",
      ],
      [
        row("organization_pending"),
        "Approve",
        "approve",
        organization && bundle === "approver",
      ],
    ] as const;
    for (const [withinRow, name, permission, allowed] of controls) {
      if (allowed)
        expect(withinRow.getByRole("button", { name })).toBeEnabled();
      else {
        const denied = withinRow.getAllByRole("button", {
          name: `Requires Risk · Confidential · ${permission}. Ask your organization owner or admin for access.`,
        });
        expect(denied).toHaveLength(permission === "edit" ? 3 : 1);
        for (const button of denied) expect(button).toBeDisabled();
      }
    }
  },
);

it.each(cases)(
  "gates organization draft editing for $bundle (organization grant: $organization)",
  async ({ bundle, organization }) => {
    mount(
      <ScenarioBuilder defaultBankId={bankId} editScenario={draft} />,
      bundle,
      organization,
    );
    const save = await screen.findByRole("button", {
      name: "Save draft",
    });
    const submit = screen.getByRole("button", {
      name: "Save & submit for approval",
    });
    if (organization && bundle === "analyst") {
      expect(save).toBeEnabled();
      expect(submit).toBeEnabled();
    } else {
      expect(save).toBeDisabled();
      expect(submit).toBeDisabled();
    }
  },
);

it.each([true, false])(
  "saves new scenarios only in their authorized scope (organization grant: %s)",
  async (organization) => {
    const bodies: unknown[] = [];
    server.use(
      http.post(`${apiBaseUrl}/macro-scenarios`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          { ...draft, id: "created_scenario" },
          { status: 201 },
        );
      }),
    );
    mount(<ScenarioBuilder defaultBankId={bankId} />, "analyst", organization);
    const scope = await screen.findByRole("checkbox", {
      name: /Scope to this bank only/,
    });
    expect(scope).toHaveProperty("checked", !organization);
    const user = userEvent.setup();
    if (!organization) {
      await user.click(scope);
      expect(
        screen.getByRole("button", { name: "Save as draft" }),
      ).toBeDisabled();
      expect(
        screen.getByRole("button", {
          name: "Save & submit for approval",
        }),
      ).toBeDisabled();
      expect(bodies).toEqual([]);
      await user.click(scope);
    }
    await user.type(
      screen.getByRole("textbox", { name: "Code (slug)" }),
      "authorized_scenario",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Authorized scenario",
    );
    await user.click(screen.getByRole("button", { name: "Mild slowdown" }));
    await user.type(
      screen.getByRole("textbox", { name: /Change reason/ }),
      "Scope regression verification",
    );
    await user.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ bank_id: organization ? null : bankId });
  },
);

it("does not turn organization Risk capabilities into account or settings access", () => {
  fc.assert(
    fc.property(
      fc.subarray<Permission>([
        "view",
        "create",
        "edit",
        "run",
        "validate",
        "export",
        "review",
        "approve",
        "administer",
      ]),
      (selected) => {
        const authority: EffectiveAuthorityRead = {
          authv: 1,
          institutionCapabilities: [],
          organizationCapabilities: selected.map((permission) => ({
            module: "risk",
            sensitivity: "restricted",
            permission,
            requiresContextualAuthorization: false,
            dataScope: { kind: "all", branches: [], regions: [] },
          })),
        };
        expect(hasAccountAdministrationAuthority(authority)).toBe(false);
        expect(hasAccountDirectoryAuthority(authority)).toBe(false);
        expect(hasGrantAdministrationAuthority(authority)).toBe(false);
        expect([
          ...effectiveOrganizationModules(authority.organizationCapabilities),
        ]).toEqual([]);
      },
    ),
  );
});
