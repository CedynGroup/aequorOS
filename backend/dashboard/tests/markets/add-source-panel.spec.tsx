// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

/**
 * The market-data source panel's save/automatic-test split after the #172
 * cutover (W6/W7). Saving a vendor connection and running its representative
 * /test pull are both MARKETS/restricted CONFIGURE duties. The pull's RESULT
 * is read material: vendor sample values and errors surface only to a caller
 * who also holds MARKETS/restricted VIEW, so a configure-only principal gets
 * the write-only acknowledgement — the success flag, no vendor content, no
 * error — which must not render as a failure. Scalar roles grant nothing.
 */

import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";

const fixture = vi.hoisted(() => ({
  capabilities: [] as EffectiveCapabilityRead[],
}));

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    error: null,
    profile: {},
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: "BK-SAMP0001",
          capabilities: fixture.capabilities,
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

function marketCapability(
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  permission: EffectiveCapabilityRead["permission"],
): EffectiveCapabilityRead {
  return {
    module: "markets",
    sensitivity,
    permission,
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
  };
}

const connection = {
  id: "md-conn",
  status: "ACTIVE",
  vendor: "bloomberg",
  validation_error: null,
};

beforeEach(() => {
  fixture.capabilities = [marketCapability("restricted", "configure")];
  createConnection.mockResolvedValue(connection);
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

it("runs the automatic test pull on the configure duty alone and renders the write-only acknowledgement", async () => {
  // W7: the configure-only caller gets the success flag with no vendor
  // content and no error — saved and tested, never rendered as a failure.
  testPull.mockResolvedValue({
    success: true,
    sampleValues: {},
    error: null,
  });
  await saveVendorConnection();
  await waitFor(() => expect(testPull).toHaveBeenCalledWith("md-conn"));
  expect(screen.getByText(/Test pull completed/i)).toBeVisible();
  expect(screen.queryByText(/Test pull did not succeed/i)).toBeNull();
  // No vendor content leaked into the write-only acknowledgement.
  expect(screen.queryByText(/open:/)).toBeNull();
});

it("shows the full test result only with the restricted view binding", async () => {
  fixture.capabilities = [
    marketCapability("restricted", "configure"),
    marketCapability("restricted", "view"),
  ];
  testPull.mockResolvedValue({
    success: true,
    sampleValues: { open: "1" },
    error: null,
  });
  await saveVendorConnection();
  await waitFor(() => expect(testPull).toHaveBeenCalledWith("md-conn"));
  expect(screen.getByText(/Configuration check complete/i)).toBeVisible();
  expect(screen.getByText(/open: 1/)).toBeVisible();
});

it("renders a failed pull without vendor detail for a configure-only principal", async () => {
  testPull.mockResolvedValue({
    success: false,
    sampleValues: {},
    error: null,
  });
  await saveVendorConnection();
  await waitFor(() => expect(testPull).toHaveBeenCalledWith("md-conn"));
  expect(screen.getByText(/Test pull did not succeed/i)).toBeVisible();
  expect(screen.queryByText(/Live vendor transport unavailable\./)).toBeNull();
});

it("keeps the save gate on the configure duty and omits the pull without it", async () => {
  fixture.capabilities = [marketCapability("restricted", "view")];
  render(
    <AddSourcePanel
      bankId="BK-SAMP0001"
      existingVendors={[]}
      onDone={() => undefined}
    />,
  );
  const user = userEvent.setup();
  await user.click((screen.getAllByRole("button", { name: /Bloomberg/ }))[0]);
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
  const save = screen.getByRole("button", {
    name: /Save vendor configuration/,
  });
  expect(save).toBeDisabled();
  expect(
    screen.getByText(/Requires Markets configure access with Restricted data/i),
  ).toBeVisible();
  expect(createConnection).not.toHaveBeenCalled();
  expect(testPull).not.toHaveBeenCalled();
});
