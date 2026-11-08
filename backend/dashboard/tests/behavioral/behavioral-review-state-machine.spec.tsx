// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import fc from "fast-check";
import type { BehavioralProposalRead } from "@aequoros/risk-service-api";
import BehavioralProposalQueue from "../../components/behavioral/BehavioralProposalQueue";
import { providers } from "../bi/testing/support";

/**
 * Stateful property coverage for the maker-checker review workflow
 * (backend/dashboard/AGENTS.md Testing): commands drive the queue through
 * acceptance, refusal-then-retry and the maker-checker restriction, and the
 * invariants observe the VISIBLE transition the real query/mutation hooks
 * render after each step. Only the transport is mocked — the list query, the
 * review mutation, its invalidation-driven refetch and the access derivation
 * all run for real, so the pending-to-accepted transition shown to the
 * reviewer is the one the API contract produces.
 */

const BANK = "BK-SAMP0001";
const MODEL = "nmd-duration";
const PROPOSER = "11111111-1111-4111-8111-111111111111";
const REVIEWER = "22222222-2222-4222-8222-222222222222";

type Status = "pending" | "accepted";

const boundary = vi.hoisted(() => ({
  actor: undefined as string | undefined,
  statuses: [] as Array<{ id: string; status: "pending" | "accepted" }>,
  failNext: false,
  reviews: [] as string[],
}));

function proposalWire(index: number): BehavioralProposalRead {
  return {
    proposalId: boundary.statuses[index].id,
    bankId: BANK,
    model: MODEL,
    modelVersion: "model-v7",
    asOfDate: new Date("2026-09-30T00:00:00Z"),
    values: [
      { productCode: "CURRENT", value: 18, unit: "months", confidence: 0.8 },
    ],
    status: boundary.statuses[index].status,
    proposedByUserId: PROPOSER,
    proposedAt: new Date("2026-11-07T09:00:00Z"),
    reviewedByUserId:
      boundary.statuses[index].status === "accepted" ? REVIEWER : undefined,
  };
}

vi.mock("../../lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api/client")>();
  const { ApiError } = actual;
  const refusal = (status: number, message: string) =>
    new ApiError({
      message,
      status,
      code: "http_error",
      errorCode: null,
      details: {},
    });
  return {
    ...actual,
    behavioralModelsApi: {
      ...actual.behavioralModelsApi,
      listBehavioralModelProposals: async () => ({
        proposals: boundary.statuses.map((_row, index) => proposalWire(index)),
      }),
      reviewBehavioralModelProposal: async ({
        proposalId,
      }: {
        proposalId: string;
      }) => {
        boundary.reviews.push(proposalId);
        if (boundary.failNext)
          throw refusal(503, "review channel refused");
        const row = boundary.statuses.find((entry) => entry.id === proposalId);
        if (!row) throw refusal(404, "Proposal not found");
        if (row.status !== "pending")
          throw refusal(409, "Only a pending proposal can be accepted");
        if (boundary.actor === PROPOSER)
          throw refusal(
            403,
            "The proposer cannot review their own proposal",
          );
        row.status = "accepted";
        return { proposalId, status: "accepted" };
      },
    },
  };
});
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    profile: { userId: boundary.actor },
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: BANK,
          capabilities: [
            {
              module: "beh",
              sensitivity: "restricted",
              permission: "view",
              requiresContextualAuthorization: false,
              dataScope: { kind: "all", branches: [], regions: [] },
            },
            {
              module: "beh",
              sensitivity: "restricted",
              permission: "approve",
              requiresContextualAuthorization: true,
              dataScope: { kind: "all", branches: [], regions: [] },
            },
          ],
        },
      ],
      organizationCapabilities: [],
    },
    isLoading: false,
    error: null,
  }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));

type Model = {
  actor: string | undefined;
  statuses: Status[];
  /** Index of the card a refused review left its error on, else null. */
  errorOn: number | null;
};

type Real = {
  user: ReturnType<typeof userEvent.setup>;
  rerender: () => void;
};

function mountQueue(real: Real) {
  const { wrapper } = providers();
  const queue = <BehavioralProposalQueue bankId={BANK} model={MODEL} />;
  const view = render(queue, { wrapper });
  real.rerender = () => view.rerender(queue);
}

function firstPending(model: Model): number {
  return model.statuses.findIndex((status) => status === "pending");
}

function reviewButtonOf(index: number): HTMLElement {
  return within(
    screen.getAllByRole("article")[index] as HTMLElement,
  ).getByRole("button", { name: "Review and accept" });
}

/** The visible state must mirror the model after every command. */
async function observe(model: Model) {
  const cards = screen.getAllByRole("article");
  expect(cards).toHaveLength(model.statuses.length);
  for (const [index, status] of model.statuses.entries()) {
    const card = within(cards[index] as HTMLElement);
    expect(card.getByText(status)).toBeVisible();
    const button = card.queryByRole("button", { name: "Review and accept" });
    if (status === "accepted") {
      expect(button).toBeNull();
      expect(
        card.getByText(new RegExp(`Accepted by ${REVIEWER}`)),
      ).toBeVisible();
    } else {
      expect(button).not.toBeNull();
      if (model.actor === PROPOSER) {
        expect(button).toBeDisabled();
        button?.parentElement?.focus();
        await waitFor(() =>
          expect(button).toHaveAccessibleDescription(
            /A different authorized reviewer is required; the proposer cannot review their own proposal/i,
          ),
        );
      } else {
        expect(button).toBeEnabled();
      }
      if (model.errorOn === index) {
        expect(
          card.getByText(/Could not review — review channel refused/i),
        ).toBeVisible();
      } else {
        expect(card.queryByText(/Could not review —/i)).toBeNull();
      }
    }
  }
}

class ReviewCommand implements fc.AsyncCommand<Model, Real> {
  check(model: Model): boolean {
    return firstPending(model) >= 0;
  }
  async run(model: Model, real: Real): Promise<void> {
    const index = firstPending(model);
    if (index < 0) return; // both accepted while this command was scheduled
    const reviewsBefore = boundary.reviews.length;
    if (model.actor !== REVIEWER) {
      // Maker-checker: the proposer's control refuses without a request, and
      // the server state the guard protects stays untouched.
      expect(reviewButtonOf(index)).toBeDisabled();
      await real.user.click(reviewButtonOf(index));
      expect(boundary.reviews.length).toBe(reviewsBefore);
      expect(boundary.statuses[index].status).toBe("pending");
      await observe(model);
      return;
    }
    await real.user.click(reviewButtonOf(index));
    await waitFor(() =>
      expect(
        within(screen.getAllByRole("article")[index] as HTMLElement).getByText(
          "accepted",
        ),
      ).toBeVisible(),
    );
    model.statuses[index] = "accepted";
    model.errorOn = null;
    await observe(model);
  }
  toString() {
    return "review";
  }
}

class FailThenRetryCommand implements fc.AsyncCommand<Model, Real> {
  check(model: Model): boolean {
    return firstPending(model) >= 0 && model.actor === REVIEWER;
  }
  async run(model: Model, real: Real): Promise<void> {
    const index = firstPending(model);
    if (index < 0) return; // both accepted while this command was scheduled
    if (model.actor !== REVIEWER) {
      // Maker-checker runtime state: the control refuses without a request,
      // so no channel failure is ever surfaced.
      const reviewsBefore = boundary.reviews.length;
      await real.user.click(reviewButtonOf(index));
      expect(boundary.reviews.length).toBe(reviewsBefore);
      await observe(model);
      return;
    }
    boundary.failNext = true;
    await real.user.click(reviewButtonOf(index));
    await waitFor(() =>
      expect(
        within(screen.getAllByRole("article")[index] as HTMLElement).getByText(
          /Could not review — review channel refused/i,
        ),
      ).toBeVisible(),
    );
    expect(boundary.statuses[index].status).toBe("pending");
    model.errorOn = index;
    await observe(model);

    boundary.failNext = false;
    await real.user.click(reviewButtonOf(index));
    await waitFor(() =>
      expect(
        within(screen.getAllByRole("article")[index] as HTMLElement).getByText(
          "accepted",
        ),
      ).toBeVisible(),
    );
    model.statuses[index] = "accepted";
    model.errorOn = null;
    await observe(model);
  }
  toString() {
    return "failThenRetry";
  }
}

class BecomeProposerCommand implements fc.AsyncCommand<Model, Real> {
  check(model: Model): boolean {
    return model.actor !== PROPOSER;
  }
  async run(model: Model, real: Real): Promise<void> {
    boundary.actor = PROPOSER;
    model.actor = PROPOSER;
    real.rerender();
    await observe(model);
  }
  toString() {
    return "becomeProposer";
  }
}

class BecomeReviewerCommand implements fc.AsyncCommand<Model, Real> {
  check(model: Model): boolean {
    return model.actor !== REVIEWER;
  }
  async run(model: Model, real: Real): Promise<void> {
    boundary.actor = REVIEWER;
    model.actor = REVIEWER;
    real.rerender();
    await observe(model);
  }
  toString() {
    return "becomeReviewer";
  }
}

const command = fc.constantFrom(
  new ReviewCommand(),
  new FailThenRetryCommand(),
  new BecomeProposerCommand(),
  new BecomeReviewerCommand(),
);

it("accepts, refuses-and-retries and restricts the maker-checker queue through the real hooks", async () => {
  const run = async (sequence: Iterable<fc.AsyncCommand<Model, Real>>) => {
    boundary.actor = REVIEWER;
    boundary.statuses = [
      { id: "33333333-3333-4333-8333-333333333331", status: "pending" },
      { id: "33333333-3333-4333-8333-333333333332", status: "pending" },
    ];
    boundary.failNext = false;
    boundary.reviews = [];
    const model: Model = {
      actor: REVIEWER,
      statuses: ["pending", "pending"],
      errorOn: null,
    };
    const real: Real = { user: userEvent.setup(), rerender: () => {} };
    cleanup();
    mountQueue(real);
    try {
      await waitFor(() => expect(reviewButtonOf(0)).toBeEnabled());
      for (const step of sequence) await step.run(model, real);
      await observe(model);
    } finally {
      cleanup();
    }
  };
  // A guaranteed full path complements the random interleavings: both
  // proposals accepted across a refusal/retry, with the maker-checker
  // restriction visited in between.
  await run([
    new FailThenRetryCommand(),
    new BecomeProposerCommand(),
    new BecomeReviewerCommand(),
    new ReviewCommand(),
  ]);
  await fc.assert(
    fc.asyncProperty(fc.commands([command], { maxCommands: 12 }), run),
    { numRuns: 20 },
  );
}, 30_000);
