// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ProfileProvider from "../components/profile/ProfileProvider";
import AccessTabs from "../components/access/AccessTabs";
import AccessPage from "../app/(app)/access/page";
import AccessMembersPage from "../app/(app)/access/members/page";
import { setAccessToken } from "../lib/api/token";

const replace = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  usePathname: () => "/access/members",
  useRouter: () => ({ replace }),
}));
vi.mock("next-auth/react", () => ({
  useSession: () => ({
    status: "authenticated",
    data: {
      accessToken: "synthetic-session",
      user: {
        organizationId: "OR-DEM00001",
        email: "admin@example.test",
        authorizationVersion: 3,
      },
    },
  }),
  signOut: vi.fn(),
}));

afterEach(() => {
  cleanup();
  setAccessToken(null);
  vi.unstubAllGlobals();
  replace.mockClear();
});

it("lands a delegated administrator on My access and explains the Members refusal before fetching members", async () => {
  const forbiddenRequests: string[] = [];
  setAccessToken("synthetic-session");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith("/auth/me")) {
        return Response.json({
          user_id: "eeeeeeee-6666-4eee-8eee-eeeeeeeeeee6",
          organization_id: "OR-DEM00001",
          email: "admin@example.test",
          display_name: "Delegated administrator",
          job_title: null,
          locale: null,
          timezone: null,
          theme: null,
          role: "account_admin",
          auth_provider: "local",
          effective_authority: {
            authv: 3,
            can_administer_grants: false,
            institution_capabilities: [],
            organization_capabilities: [
              {
                module: "account",
                sensitivity: "restricted",
                permission: "administer",
                requires_contextual_authorization: false,
                data_scope: { kind: "all", branches: [], regions: [] },
              },
            ],
          },
        });
      }
      forbiddenRequests.push(url);
      return Response.json(
        {
          error: {
            code: "forbidden",
            message: "This action requires Organization Owner authority.",
          },
        },
        { status: 403 },
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  try {
    render(
      <QueryClientProvider client={client}>
        <ProfileProvider>
          <AccessPage />
          <AccessTabs />
          <AccessMembersPage />
        </ProfileProvider>
      </QueryClientProvider>,
    );
    expect(
      await screen.findByText(
        "Only organization owners can manage members and grants.",
      ),
    ).toBeVisible();
    expect(replace).toHaveBeenCalledWith("/access/my-access");
    expect(screen.getByRole("link", { name: "Members" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      screen.getByRole("link", { name: "Authentication" }),
    ).toHaveAttribute("href", "/access/authentication");
    expect(
      screen.getByRole("link", { name: "Integration keys" }),
    ).toHaveAttribute("href", "/access/integration-keys");
    expect(forbiddenRequests).toEqual([]);
  } finally {
    client.clear();
  }
});
