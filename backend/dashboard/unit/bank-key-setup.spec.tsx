// @vitest-environment jsdom
import React from "react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import fc from "fast-check";
import BankKeyPanel, {
  BankKeySetup,
} from "../components/settings/BankKeyPanel";

const boundary = vi.hoisted(() => ({
  status: vi.fn(),
  probe: vi.fn(),
  connect: vi.fn(),
  rotate: vi.fn(),
  confirm: vi.fn(),
  authority: false,
}));
vi.mock("@aequoros/risk-service-api", () => ({
  BankEncryptionApi: class {
    getBankKeyStatus = boundary.status;
    testBankKeyConnection = boundary.probe;
    connectBankKey = boundary.connect;
    rotateBankKey = boundary.rotate;
    confirmBankKeyRotationSchedule = boundary.confirm;
  },
}));
vi.mock("../lib/api/client", () => ({
  configuration: {},
  apiCall: (fn: () => Promise<unknown>) => fn(),
}));
vi.mock("../components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ effectiveAuthority: boundary.authority }),
}));
vi.mock("../lib/api/accountAdministration", () => ({
  hasAccountAdministrationAuthority: (value: boolean) => value,
}));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTHETIC" } }),
}));
const arn = "arn:aws:kms:us-east-1:123456789012:key/synthetic";
const disconnected = {
  connected: false,
  workloadRoleArn: "arn:aws:iam::444455556666:role/synthetic-workload",
};
const connected = {
  ...disconnected,
  connected: true,
  currentVersion: "version-one",
  key: {
    keyId: arn,
    provider: "aws_kms",
    ownerAccount: "123456789012",
    region: "us-east-1",
    status: "active",
    checkedAt: new Date("2026-10-23T00:00:00Z"),
  },
};

beforeEach(() => {
  vi.stubGlobal("React", React);
  vi.clearAllMocks();
  boundary.authority = false;
  boundary.status.mockResolvedValue(disconnected);
  boundary.probe.mockResolvedValue({
    passed: true,
    message: "Synthetic round trip passed.",
  });
  boundary.connect.mockResolvedValue(connected);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(element = <BankKeySetup bankId="BK-SYNTHETIC" />) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      {element}
    </QueryClientProvider>,
  );
}
async function fill() {
  await screen.findByText("Connect your bank key");
  fireEvent.change(screen.getByLabelText("Exact key ARN"), {
    target: { value: arn },
  });
  fireEvent.change(screen.getByLabelText("Bank AWS account ID"), {
    target: { value: "123456789012" },
  });
  fireEvent.change(screen.getByLabelText("AWS region"), {
    target: { value: "us-east-1" },
  });
}
it("keeps day-to-day users from seeing controls or requesting key metadata", () => {
  mount(<BankKeyPanel />);
  expect(screen.queryByText("Bank encryption key")).toBeNull();
  expect(boundary.status).not.toHaveBeenCalled();
});
it("requires a successful current probe and connects using only the key reference", async () => {
  mount();
  await fill();
  expect(
    screen.getByRole("button", { name: "Connect bank key" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await screen.findByText("Synthetic round trip passed.");
  fireEvent.click(screen.getByRole("button", { name: "Connect bank key" }));
  await screen.findByText(
    "Bank key connected. Setup recorded in the audit log.",
  );
  expect(boundary.connect).toHaveBeenCalledWith({
    bankId: "BK-SYNTHETIC",
    bankKeyConnect: {
      provider: "aws_kms",
      keyId: arn,
      ownerAccount: "123456789012",
      region: "us-east-1",
    },
  });
  expect(screen.getByText("version-one")).toBeVisible();
});
it("reports actionable test failures without offering connect", async () => {
  boundary.probe.mockResolvedValue({
    passed: false,
    message: "Access denied for kms:Decrypt. Add decrypt permission.",
  });
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("kms:Decrypt");
  expect(
    screen.getByRole("button", { name: "Connect bank key" }),
  ).toBeDisabled();
});
it("shows exact policy with the configured workload role", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  mount();
  await fill();
  fireEvent.click(
    screen.getByRole("button", { name: "Copy bank key policy statement" }),
  );
  await waitFor(() => expect(writeText).toHaveBeenCalled());
  const policy = JSON.parse(writeText.mock.calls[0][0]);
  expect(policy.Principal.AWS).toBe(disconnected.workloadRoleArn);
  expect(policy.Action).toEqual([
    "kms:DescribeKey",
    "kms:GenerateDataKey",
    "kms:Encrypt",
    "kms:Decrypt",
  ]);
});
it("records only an explicit bank confirmation and labels scheduling unverified", async () => {
  boundary.status.mockResolvedValue(connected);
  boundary.confirm.mockResolvedValue({
    ...connected,
    rotationPeriodDays: 365,
    rotationConfirmedAt: new Date(),
  });
  mount();
  const button = await screen.findByRole("button", {
    name: "Record schedule confirmation",
  });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(button);
  await screen.findByText(/Bank-reported schedule: every 365 days/);
  expect(boundary.confirm).toHaveBeenCalledWith(
    expect.objectContaining({
      bankKeyScheduleConfirm: expect.objectContaining({
        keyId: arn,
        periodDays: 365,
      }),
    }),
  );
  expect(
    screen.getByText(/Not independently verified by aequorOS/),
  ).toBeVisible();
});
it("invalidates the probe after edited references in a generated transition sequence", async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.array(fc.boolean(), { minLength: 1, maxLength: 5 }),
      async (actions) => {
        mount();
        await fill();
        let tested = false;
        let revision = 0;
        for (const probe of actions) {
          if (probe) {
            fireEvent.click(
              screen.getByRole("button", { name: "Test connection" }),
            );
            await screen.findByText("Synthetic round trip passed.");
            tested = true;
          } else {
            revision++;
            fireEvent.change(screen.getByLabelText("Exact key ARN"), {
              target: { value: `${arn}-${revision}` },
            });
            tested = false;
          }
          await waitFor(() =>
            expect(
              screen.getByRole("button", { name: "Connect bank key" }),
            ).toHaveProperty("disabled", !tested),
          );
        }
        cleanup();
      },
    ),
    { numRuns: 8 },
  );
});

it("requires acknowledgement of the refreshed key and edited interval", async () => {
  boundary.status.mockResolvedValue(connected);
  const replacement = {
    ...connected,
    currentVersion: "version-two",
    key: { ...connected.key, keyId: `${arn}-replacement` },
  };
  boundary.confirm.mockResolvedValue(replacement);
  mount();
  const button = await screen.findByRole("button", {
    name: "Record schedule confirmation",
  });
  fireEvent.click(screen.getByRole("checkbox"));
  expect(button).toBeEnabled();
  boundary.status.mockResolvedValue(replacement);
  fireEvent.click(screen.getByRole("button", { name: "Test connected key" }));
  await screen.findByText(replacement.key.keyId);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(boundary.confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(
    screen.getByLabelText("Rotation interval in days (90–2560)"),
    {
      target: { value: "180" },
    },
  );
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(button);
  await waitFor(() =>
    expect(boundary.confirm).toHaveBeenCalledWith({
      bankId: "BK-SYNTHETIC",
      bankKeyScheduleConfirm: {
        keyId: replacement.key.keyId,
        periodDays: 180,
        reason: "Bank administrator confirms native AWS rotation was enabled.",
      },
    }),
  );
});
