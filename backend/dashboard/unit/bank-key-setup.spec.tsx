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
  await screen.findByLabelText("Exact key ARN");
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
it("lets an authorized administrator load the bank's key settings", async () => {
  boundary.authority = true;
  mount(<BankKeyPanel />);
  expect(await screen.findByText("Connect your bank key")).toBeVisible();
  expect(boundary.status).toHaveBeenCalledWith({ bankId: "BK-SYNTHETIC" });
});
it("recovers from a status request failure through Retry status", async () => {
  boundary.status.mockRejectedValueOnce(new Error("Key status unavailable."));
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load key status: Key status unavailable.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry status" }));
  expect(await screen.findByText("Connect your bank key")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
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
it("shows a connected-key probe refusal and refreshes the recorded state", async () => {
  boundary.status.mockResolvedValue(connected);
  boundary.probe.mockResolvedValue({
    passed: false,
    message: "Key disabled. Enable the key in AWS KMS.",
  });
  mount();
  const button = await screen.findByRole("button", {
    name: "Test connected key",
  });
  boundary.status.mockResolvedValue({
    ...connected,
    key: { ...connected.key, status: "disabled" },
  });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Key disabled. Enable the key in AWS KMS.",
  );
  expect(await screen.findByText(/^disabled ·/)).toBeVisible();
  expect(boundary.probe).toHaveBeenCalledWith({
    bankId: "BK-SYNTHETIC",
    bankKeyConnect: {
      provider: "aws_kms",
      keyId: arn,
      ownerAccount: "123456789012",
      region: "us-east-1",
    },
  });
  expect(button).toBeEnabled();
});
it("rotates only after a successful probe and an adequate reason, then clears the form", async () => {
  const replacement = {
    ...connected,
    currentVersion: "version-two",
    key: { ...connected.key, keyId: `${arn}-replacement` },
    lastRotatedAt: new Date("2026-10-23T12:00:00Z"),
  };
  boundary.status.mockResolvedValue(connected);
  boundary.rotate.mockResolvedValue(replacement);
  mount();
  await fill();
  fireEvent.change(screen.getByLabelText("Exact key ARN"), {
    target: { value: replacement.key.keyId },
  });
  const button = screen.getByRole("button", { name: "Rotate bank key" });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await screen.findByText("Synthetic round trip passed.");
  expect(button).toBeDisabled();
  const reason = screen.getByLabelText("Reason for replacement-key rotation");
  fireEvent.change(reason, { target: { value: "short" } });
  expect(button).toBeDisabled();
  fireEvent.change(reason, {
    target: { value: "  Scheduled key replacement.  " },
  });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(
    await screen.findByText(
      "Replacement key connected. Rotation recorded in the audit log.",
    ),
  ).toBeVisible();
  expect(boundary.rotate).toHaveBeenCalledWith({
    bankId: "BK-SYNTHETIC",
    bankKeyRotate: {
      provider: "aws_kms",
      keyId: replacement.key.keyId,
      ownerAccount: "123456789012",
      region: "us-east-1",
      reason: "Scheduled key replacement.",
    },
  });
  expect(screen.getByText("version-two")).toBeVisible();
  expect(
    screen.getByText(replacement.lastRotatedAt.toLocaleString()),
  ).toBeVisible();
  for (const label of ["Exact key ARN", "Bank AWS account ID", "AWS region"]) {
    expect(screen.getByLabelText(label)).toHaveValue("");
  }
  expect(reason).toHaveValue("");
  expect(button).toBeDisabled();
  expect(boundary.connect).not.toHaveBeenCalled();
});

it.each([
  "Test connection",
  "Connect bank key",
  "Rotate bank key",
  "Test connected key",
  "Record schedule confirmation",
] as const)("reports request errors and releases controls for %s", async (action) => {
  const connectedAction = !["Test connection", "Connect bank key"].includes(
    action,
  );
  boundary.status.mockResolvedValue(connectedAction ? connected : disconnected);
  mount();
  await fill();
  if (action === "Rotate bank key") {
    fireEvent.change(
      screen.getByLabelText("Reason for replacement-key rotation"),
      { target: { value: "Scheduled key replacement." } },
    );
  }
  if (action === "Connect bank key" || action === "Rotate bank key") {
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText("Synthetic round trip passed.");
  }
  if (action === "Record schedule confirmation") {
    fireEvent.click(screen.getByRole("checkbox"));
  }
  const request = {
    "Test connection": boundary.probe,
    "Connect bank key": boundary.connect,
    "Rotate bank key": boundary.rotate,
    "Test connected key": boundary.probe,
    "Record schedule confirmation": boundary.confirm,
  }[action];
  request.mockRejectedValueOnce(
    new Error("AWS request unavailable. Retry shortly."),
  );
  const button = screen.getByRole("button", { name: action });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "AWS request unavailable. Retry shortly.",
  );
  expect(button).toBeEnabled();
});
it("reports a retry instruction when a request rejects without an Error", async () => {
  boundary.probe.mockRejectedValueOnce(null);
  mount();
  await fill();
  const button = screen.getByRole("button", { name: "Test connection" });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The request failed. Retry the operation.",
  );
  expect(button).toBeEnabled();
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
