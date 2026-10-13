// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  type RenderResult,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import fc from "fast-check";
import type { PackageStatus } from "@aequoros/risk-service-api";
import {
  filingTransitions as transitions,
  filingStatuses,
  filingStatusArbitrary,
  officers,
  officerArbitrary,
  officerProjection as projection,
  type Officer,
} from "./submissions/testing/generators";
import { assertFilingReceipt } from "./submissions/testing/assertions";
import { PrimaryActionButton } from "../components/submissions/ReturnCommandBar";
import { filingAuthorityFor } from "../lib/submissions/filingAuthority";
import {
  noActionExplanation,
  primaryFilingAction,
  type SurfaceInput,
} from "../lib/submissions/returnsSurface";

afterEach(cleanup);

it("does not offer preparation or approval again while the regulator holds the filing", () => {
  for (const officer of ["preparer", "approver"] as const) {
    const model: Model = {
      status: "submitted",
      officer,
      bank: "BK-A",
      resolved: true,
      readOnly: false,
      cleared: true,
      pending: false,
    };
    const real: Real = {
      view: render(<></>),
      user: userEvent.setup(),
      receipt: null,
      onAct: (label) => {
        real.receipt = label;
      },
    };
    real.view.rerender(<FilingControl model={model} onAct={real.onAct} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "This return is with Test regulator. Nothing is asked of the bank until they decide.",
      ),
    ).toBeVisible();
    cleanup();
  }
});

type Model = {
  status: PackageStatus;
  officer: Officer;
  bank: "BK-A" | "BK-B";
  resolved: boolean;
  readOnly: boolean;
  cleared: boolean;
  pending: boolean;
};
type Real = {
  view: RenderResult;
  user: ReturnType<typeof userEvent.setup>;
  receipt: string | null;
  onAct: (label: string) => void;
};

function FilingControl({
  model,
  onAct,
}: {
  model: Model;
  onAct: Real["onAct"];
}) {
  const input: SurfaceInput = {
    authority: filingAuthorityFor(projection(model.officer), model.bank, {
      resolved: model.resolved,
      readOnly: model.readOnly,
    }),
    status: model.status,
    hasPackage: true,
    checksClean: true,
    checkErrors: 0,
    clearedToSubmit: model.cleared,
    outstandingSummary: "1 validator signature",
    signingRequired: true,
    isRehearsal: false,
    pendingReupload: false,
    canPoll: true,
    regulatorName: "Test regulator",
  };
  const action = primaryFilingAction(input);
  return action ? (
    <PrimaryActionButton
      action={action}
      pending={model.pending}
      onClick={() => {
        onAct(action.label);
      }}
    />
  ) : (
    <p>{noActionExplanation(input)}</p>
  );
}

// Independent user-facing expectations: an earlier officer's act disappears
// after hand-off, and the next officer's act waits for that hand-off.
function expectedAct(model: Model): { label: string; enabled: boolean } | null {
  if (
    !model.resolved ||
    model.readOnly ||
    model.bank === "BK-B" ||
    model.officer === "reader"
  )
    return null;
  if (["acknowledged", "declined", "superseded"].includes(model.status))
    return null;
  if (model.officer === "preparer") {
    if (model.status === "rejected")
      return { label: "Generate a corrected version", enabled: !model.pending };
    if (["draft", "generated", "validated"].includes(model.status)) {
      return {
        label: "Certify and freeze",
        enabled: model.status === "validated" && !model.pending,
      };
    }
    return null;
  }
  if (model.officer === "approver") {
    if (["approved", "submitted"].includes(model.status)) return null;
    return {
      label: "Review and sign",
      enabled: model.status === "pending_approval" && !model.pending,
    };
  }
  return model.status === "submitted"
    ? { label: "Check for Test regulator's decision", enabled: !model.pending }
    : {
        label: "File with Test regulator",
        enabled: model.status === "approved" && model.cleared && !model.pending,
      };
}

function observe(model: Model) {
  const expected = expectedAct(model);
  if (!expected) {
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    if (
      ["acknowledged", "declined", "superseded"].includes(model.status) &&
      model.resolved &&
      !model.readOnly
    ) {
      expect(
        screen.getByText(/finished its chain|held by other officers/),
      ).toBeVisible();
    }
    return;
  }
  const button = screen.getByRole("button", { name: expected.label });
  if (expected.enabled) expect(button).toBeEnabled();
  else
    expect(
      button.matches(":disabled") ||
        button.getAttribute("aria-disabled") === "true",
    ).toBe(true);
}

class Change implements fc.AsyncCommand<Model, Real> {
  constructor(
    readonly field:
      "officer" | "bank" | "resolved" | "readOnly" | "cleared" | "pending",
    readonly value: Officer | Model["bank"] | boolean,
  ) {}
  check() {
    return true;
  }
  async run(model: Model, real: Real) {
    Object.assign(model, { [this.field]: this.value });
    real.view.rerender(<FilingControl model={model} onAct={real.onAct} />);
    observe(model);
  }
  toString() {
    return `${this.field}=${this.value}`;
  }
}

class ServerStatus implements fc.AsyncCommand<Model, Real> {
  constructor(readonly next: PackageStatus) {}
  check(model: Readonly<Model>) {
    return transitions[model.status].includes(this.next);
  }
  async run(model: Model, real: Real) {
    model.status = this.next;
    real.view.rerender(<FilingControl model={model} onAct={real.onAct} />);
    observe(model);
  }
  toString() {
    return `serverStatus=${this.next}`;
  }
}

class TryAct implements fc.AsyncCommand<Model, Real> {
  check() {
    return true;
  }
  async run(model: Model, real: Real) {
    observe(model);
    real.receipt = null;
    const button = screen.queryByRole("button");
    if (button) await real.user.click(button);
    assertFilingReceipt(
      real.receipt,
      expectedAct(model)?.enabled ? expectedAct(model)!.label : null,
    );
    // Clicking asks for an act. A server response must settle before another
    // stage is offered; the button cannot manufacture an approval or filing.
    observe(model);
  }
  toString() {
    return "tryAct";
  }
}

it("keeps filing controls scoped and frozen across generated server and officer sequences", async () => {
  const statuses = filingStatuses;
  const initial = (status: PackageStatus): Model => ({
    status,
    officer: "preparer",
    bank: "BK-A",
    resolved: true,
    readOnly: false,
    cleared: true,
    pending: false,
  });
  const setup = (model: Model): Real => {
    const real: Real = {
      view: render(<></>),
      user: userEvent.setup(),
      receipt: null,
      onAct: (label) => {
        real.receipt = label;
      },
    };
    real.view.rerender(<FilingControl model={model} onAct={real.onAct} />);
    observe(model);
    return real;
  };
  // Every status/officer checkpoint runs even if random command generation
  // does not reach it, including the terminal and frozen states.
  for (const status of statuses)
    for (const officer of officers) {
      const model = { ...initial(status), officer };
      const real = setup(model);
      await new TryAct().run(model, real);
      cleanup();
    }
  const commands = fc.commands(
    [
      filingStatusArbitrary.map((status) => new ServerStatus(status)),
      officerArbitrary.map((officer) => new Change("officer", officer)),
      fc
        .constantFrom("BK-A" as const, "BK-B" as const)
        .map((bank) => new Change("bank", bank)),
      fc
        .tuple(
          fc.constantFrom(
            "resolved" as const,
            "readOnly" as const,
            "cleared" as const,
            "pending" as const,
          ),
          fc.boolean(),
        )
        .map(([field, value]) => new Change(field, value)),
      fc.constant(new TryAct()),
    ],
    { maxCommands: 25 },
  );
  const run = async (
    status: PackageStatus,
    sequence: Iterable<fc.AsyncCommand<Model, Real>>,
  ) => {
    try {
      await fc.asyncModelRun(() => {
        const model = initial(status);
        return { model, real: setup(model) };
      }, sequence);
    } finally {
      cleanup();
    }
  };
  await run("draft", [
    new ServerStatus("generated"),
    new TryAct(),
    new ServerStatus("validated"),
    new TryAct(),
    new ServerStatus("pending_approval"),
    new TryAct(),
    new Change("officer", "approver"),
    new TryAct(),
    new ServerStatus("approved"),
    new TryAct(),
    new Change("officer", "validator"),
    new Change("cleared", false),
    new TryAct(),
    new Change("cleared", true),
    new TryAct(),
    new Change("pending", true),
    new TryAct(),
    new Change("pending", false),
    new ServerStatus("submitted"),
    new TryAct(),
    new Change("officer", "preparer"),
    new TryAct(),
    new Change("officer", "validator"),
    new ServerStatus("acknowledged"),
    new TryAct(),
    new Change("officer", "approver"),
    new TryAct(),
  ]);
  await fc.assert(fc.asyncProperty(filingStatusArbitrary, commands, run), {
    numRuns: 20,
  });
}, 20_000);
