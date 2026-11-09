// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

/**
 * The market-data source panel's save/automatic-test split (#184).
 *
 * Saving a vendor connection is a MARKETS configure duty; the representative
 * /test pull keeps its separate legacy operational gate (a scalar
 * admin/approver/analyst role — ``account_admin`` sits outside the ladder).
 * A principal without that role must finish the wizard with the
 * configuration saved and the automatic pull omitted — not save first and
 * then watch the pull fail with a 403.
 */

const fixture = vi.hoisted(() => ({ role: "viewer" }));

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    error: null,
    profile: { role: fixture.role },
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: "BK-SAMP0001",
          capabilities: [
            {
              module: "markets",
              sensitivity: "restricted",
              permission: "configure",
              requiresContextualAuthorization: false,
              dataScope: { kind: "all", branches: [], regions: [] },
            },
          ],
        },
      ],
    },
  }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));

const createConnection = vi.hoisted(() => vi.fn());
const testPull = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/hooks", () => ({
  useMarketDataScopes: () => ({
    data: {
      scopes: [
        {
          scope: "FX_SPOT_RATE",
          category: "FX_SPOT",
          defaultFrequency: "END_OF_DAY",
          quotaUnits: 2,
          supportedBy: ["bloomberg", "refinitiv"],
        },
      ],
    },
  }),
  useCreateMarketDataConnection: () => ({ mutateAsync: createConnection }),
  useTestMarketDataConnection: () => ({ mutateAsync: testPull }),
}));

import AddSourcePanel from "../../components/market-data/AddSourcePanel";

const connection = {
  id: "md-conn",
  status: "ACTIVE",
  vendor: "bloomberg",
  validation_error: null,
};

beforeEach(() => {
  createConnection.mockResolvedValue(connection);
  testPull.mockResolvedValue({
    success: true,
    sampleValues: { open: "1" },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function saveVendorConnection() {
  render(
    <AddSourcePanel
      bankId="BK-SAMP0001"
      existingVendors={[]}
      onDone={() => undefined}
    />,
  );
  const user = userEvent.setup();
  await user.click(
    (screen.getAllByRole("button", { name: /Bloomberg/ }))[0],
  );
  await user.click(screen.getByRole("button", { name: "Continue" }));
  for (const label of [
    "Application identifier",
    "Serial number",
    "Authentication endpoint",
    "SSL client certificate (PEM)",
    "Subscription tier",
    "Bloomberg administrator contact",
  ]) {
    await user.type(screen.getByLabelText(label), "fixture-value");
  }
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(
    screen.getByRole("button", { name: /Save vendor configuration/ }),
  );
  await waitFor(() => expect(createConnection).toHaveBeenCalled());
}

it("omits the automatic test pull for a read-only principal", async () => {
  fixture.role = "viewer";
  await saveVendorConnection();
  expect(testPull).not.toHaveBeenCalled();
  expect(
    screen.getByText(/Automatic test pull omitted/i),
  ).toBeVisible();
});

it("runs the automatic test pull for an operational principal", async () => {
  fixture.role = "analyst";
  await saveVendorConnection();
  await waitFor(() => expect(testPull).toHaveBeenCalledWith("md-conn"));
  expect(screen.queryByText(/Automatic test pull omitted/i)).toBeNull();
});

it("omits the automatic test pull for an account-only administrator", async () => {
  // ``account_admin`` sits outside the operational role ladder the backend
  // mutation gate enforces, so the pull would 403 after a successful save.
  fixture.role = "account_admin";
  await saveVendorConnection();
  expect(testPull).not.toHaveBeenCalled();
  expect(screen.getByText(/Automatic test pull omitted/i)).toBeVisible();
});
