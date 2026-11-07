// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  instanceOfStressSignoffRead,
  type StressSignoffRead,
} from "@aequoros/risk-service-api";
import { useAttestStressSignoffWithHeldAuthorization } from "../../components/stress/hooks";

vi.mock("next-auth/react", () => ({ getSession: vi.fn() }));

const payload = {
  id: "signoff-synthetic",
  organization_id: "OR-SYNTHETIC",
  bank_id: "BK-SYNTHETIC",
  run_id: "run-synthetic",
  reporting_period_id: "period-synthetic",
  scenario_code: "SYNTHETIC",
  version: 2,
  status: "attested",
  scenario_narrative: "Synthetic stress scenario",
  assumptions_rationale: "Synthetic assumptions",
  methodology_summary: null,
  credibility_rationale: "Synthetic Board rationale",
  board_challenge: null,
  attestation_recipient_user_id: "user-signer",
  attested_by: "user-signer",
  attested_at: "2026-11-07T20:00:00Z",
  created_by: "user-preparer",
  created_at: "2026-11-07T19:00:00Z",
  submitted_by: "user-preparer",
  submitted_at: "2026-11-07T19:30:00Z",
  updated_at: "2026-11-07T20:00:00Z",
  signing_digest: "sha256:synthetic-signing-digest",
  signing_authorization_id: "authorization-synthetic",
  stays_above_all_minima: true,
  with_actions_stays_above_all_minima: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(payload)));
});

it("converts the held-authorization snake_case response for its camelCase caller", async () => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const { result } = renderHook(
    () => useAttestStressSignoffWithHeldAuthorization("BK-SYNTHETIC"),
    {
      wrapper: ({ children }: PropsWithChildren) =>
        createElement(QueryClientProvider, { client }, children),
    },
  );
  const request = {
    expectedSigningDigest: payload.signing_digest,
    credibilityRationale: payload.credibility_rationale,
    boardChallenge: payload.board_challenge,
    reason: "Synthetic audit reason",
  };

  let read: StressSignoffRead | undefined;
  await act(async () => {
    read = await result.current.mutateAsync({
      signoffId: payload.id,
      body: request,
    });
  });

  expect(read).toMatchObject({
    id: payload.id,
    bankId: payload.bank_id,
    runId: payload.run_id,
    signingDigest: payload.signing_digest,
    attestationRecipientUserId: payload.attestation_recipient_user_id,
    status: payload.status,
  });
  expect(read?.attestedAt).toBe(payload.attested_at);
  expect(read?.updatedAt).toEqual(new Date(payload.updated_at));
  expect(read).toSatisfy(instanceOfStressSignoffRead);
  expect(fetch).toHaveBeenCalledWith(
    expect.stringMatching(/\/api\/stress\/signoff\/attest$/),
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        bankId: payload.bank_id,
        signoffId: payload.id,
        ...request,
      }),
    }),
  );
  client.clear();
});
