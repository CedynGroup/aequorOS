// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const signoffApi = vi.hoisted(() => ({
  createEnterpriseStressSignoff: vi.fn(),
  updateEnterpriseStressSignoff: vi.fn(),
  submitEnterpriseStressSignoff: vi.fn(),
  attestEnterpriseStressSignoff: vi.fn(),
  withdrawEnterpriseStressSignoff: vi.fn(),
  stepUpEnterpriseStressSignoff: vi.fn(),
}));

vi.mock("@/lib/api/client", async (importOriginal) => ({
  // The generated client is the boundary under test: only its sign-off API is
  // replaced, the shared error normalization stays real.
  ...(await importOriginal<Record<string, unknown>>()),
  enterpriseStressSignoffApi: signoffApi,
}));
vi.mock("next-auth/react", () => ({ getSession: vi.fn() }));

import {
  useAttestStressSignoff,
  useCreateStressSignoff,
  useStressSignoffStepUp,
  useSubmitStressSignoff,
  useUpdateStressSignoff,
  useWithdrawStressSignoff,
} from "../../components/stress/hooks";

const bankId = "BK-SAMPLE";
const signoffId = "signoff-1";
const read = { id: signoffId, status: "pending_attestation" };
let client: QueryClient;

const wrapper = ({ children }: PropsWithChildren) =>
  createElement(QueryClientProvider, { client }, children);

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  for (const mock of Object.values(signoffApi)) {
    mock.mockReset();
    mock.mockResolvedValue(read);
  }
});

afterEach(() => {
  cleanup();
  client.clear();
});

it("creates a sign-off through the generated client and invalidates sign-off queries", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { stale: true });
  const { result } = renderHook(() => useCreateStressSignoff(bankId), {
    wrapper,
  });
  const body = {
    runId: "run-1",
    assumptionsRationale: "Synthetic rationale",
    scenarioNarrative: "Synthetic scenario",
    reason: "Synthetic reason",
  };

  let response: unknown;
  await act(async () => {
    response = await result.current.mutateAsync(body);
  });

  expect(response).toBe(read);
  expect(signoffApi.createEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    stressSignoffCreate: body,
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(true);
});

it("updates a sign-off through the generated client and invalidates sign-off queries", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { stale: true });
  const { result } = renderHook(() => useUpdateStressSignoff(bankId), {
    wrapper,
  });
  const body = { reason: "Synthetic amendment reason" };

  let response: unknown;
  await act(async () => {
    response = await result.current.mutateAsync({ signoffId, body });
  });

  expect(response).toBe(read);
  expect(signoffApi.updateEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    signoffId,
    stressSignoffUpdate: body,
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(true);
});

it("submits a sign-off to its named recipient and invalidates sign-off queries", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { stale: true });
  const { result } = renderHook(() => useSubmitStressSignoff(bankId), {
    wrapper,
  });
  const body = {
    attestationRecipientUserId: "user-signer",
    reason: "Synthetic routing reason",
  };

  let response: unknown;
  await act(async () => {
    response = await result.current.mutateAsync({ signoffId, body });
  });

  expect(response).toBe(read);
  expect(signoffApi.submitEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    signoffId,
    stressSignoffSubmission: body,
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(true);
});

it("attests a sign-off with the one-shot token and invalidates sign-off queries", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { stale: true });
  const { result } = renderHook(() => useAttestStressSignoff(bankId), {
    wrapper,
  });
  const body = {
    authorizationToken: "one-shot-token",
    expectedSigningDigest: "digest-1",
    credibilityRationale: "Synthetic Board rationale",
    boardChallenge: null,
    reason: "Synthetic attestation reason",
  };

  let response: unknown;
  await act(async () => {
    response = await result.current.mutateAsync({ signoffId, body });
  });

  expect(response).toBe(read);
  expect(signoffApi.attestEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    signoffId,
    stressSignoffAttestation: body,
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(true);
});

it("withdraws a sign-off and invalidates sign-off queries", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { stale: true });
  const { result } = renderHook(() => useWithdrawStressSignoff(bankId), {
    wrapper,
  });

  let response: unknown;
  await act(async () => {
    response = await result.current.mutateAsync({
      signoffId,
      body: { reason: "Synthetic withdrawal reason" },
    });
  });

  expect(response).toBe(read);
  expect(signoffApi.withdrawEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    signoffId,
    stressSignoffTransition: { reason: "Synthetic withdrawal reason" },
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(true);
});

it("steps up without invalidating sign-off queries (no sign-off state changed yet)", async () => {
  client.setQueryData(["stress-signoffs", "scope", bankId], { fresh: true });
  const { result } = renderHook(() => useStressSignoffStepUp(bankId), {
    wrapper,
  });

  await act(async () => {
    await result.current.mutateAsync({
      signoffId,
      body: { password: "synthetic-password" },
    });
  });

  expect(signoffApi.stepUpEnterpriseStressSignoff).toHaveBeenCalledWith({
    bankId,
    signoffId,
    stressSignoffStepUpRequest: { password: "synthetic-password" },
  });
  expect(
    client.getQueryState(["stress-signoffs", "scope", bankId])?.isInvalidated,
  ).toBe(false);
});
