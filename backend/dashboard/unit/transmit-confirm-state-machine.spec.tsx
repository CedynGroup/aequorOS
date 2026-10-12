// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  within,
  type RenderResult,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import fc from "fast-check";
import TransmitConfirmDialog from "../components/submissions/TransmitConfirmDialog";
import type { TransmitPreview } from "../components/submissions/transmitPreview";
import { setActiveJurisdiction } from "../lib/format";

const preview: TransmitPreview = {
  returnCode: "SAMPLE",
  returnName: "Sample return",
  reportingDate: "2026-03-31",
  institutionName: "Sample bank",
  deadline: null,
  channelLabel: "Sample channel",
  isSimulated: true,
  institutionCode: "SAMPLE-BANK",
  submissionRevision: "revision-1",
  isFirstFiling: true,
  filingSet: [
    {
      kind: "pdf",
      filename: "sample.pdf",
      role: "signed_record",
      sizeBytes: 1024,
      generatedAtSubmission: false,
      signatureCount: 2,
    },
  ],
  satisfied: ["Two officers have signed"],
  contentDigest: null,
  omissions: [],
};
type Model = { acknowledged: boolean; pending: boolean; refused: boolean };
type Real = {
  view: RenderResult;
  user: ReturnType<typeof userEvent.setup>;
  outcome: "confirm" | "cancel" | null;
  onConfirm: () => void;
  onCancel: () => void;
};

afterEach(() => {
  cleanup();
  setActiveJurisdiction({});
});

function Dialog({
  model,
  onConfirm,
  onCancel,
}: {
  model: Model;
  onConfirm: Real["onConfirm"];
  onCancel: Real["onCancel"];
}) {
  return (
    <TransmitConfirmDialog
      preview={preview}
      regulatorName="Sample regulator"
      pending={model.pending}
      error={model.refused ? new Error("Channel unavailable") : null}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}

function observe(model: Model) {
  const dialog = within(
    screen.getByRole("dialog", { name: "File SAMPLE with Sample regulator" }),
  );
  const acknowledgment = dialog.getByRole("checkbox", {
    name: /I am filing this return/,
  });
  if (model.acknowledged) expect(acknowledgment).toBeChecked();
  else expect(acknowledgment).not.toBeChecked();
  const submit = dialog.getByRole("button", { name: "Transmit (simulated)" });
  if (model.acknowledged && !model.pending) expect(submit).toBeEnabled();
  else expect(submit).toBeDisabled();
  if (model.refused)
    expect(dialog.getByText("The filing was not accepted")).toBeVisible();
  else
    expect(
      dialog.queryByText("The filing was not accepted"),
    ).not.toBeInTheDocument();
  expect(dialog.getByText("sample.pdf")).toBeVisible();
  expect(
    dialog.getByText("Simulated — nothing is sent to Sample regulator."),
  ).toBeVisible();
}

type Step =
  "acknowledge" | "pending" | "refused" | "confirm" | "cancelAndReopen";
class DialogCommand implements fc.AsyncCommand<Model, Real> {
  constructor(
    readonly step: Step,
    readonly value: boolean,
  ) {}
  check() {
    return true;
  }
  async run(model: Model, real: Real) {
    if (this.step === "acknowledge") {
      await real.user.click(
        screen.getByRole("checkbox", { name: /I am filing this return/ }),
      );
      model.acknowledged = !model.acknowledged;
    } else if (this.step === "pending" || this.step === "refused") {
      // These are public parent inputs: network progress/refusal, not mocked
      // component state. A rerender must retain the officer's acknowledgment.
      model[this.step] = this.value;
      real.view.rerender(
        <Dialog
          model={model}
          onConfirm={real.onConfirm}
          onCancel={real.onCancel}
        />,
      );
    } else if (this.step === "confirm") {
      real.outcome = null;
      await real.user.click(
        screen.getByRole("button", { name: "Transmit (simulated)" }),
      );
      expect(real.outcome).toBe(
        model.acknowledged && !model.pending ? "confirm" : null,
      );
    } else {
      real.outcome = null;
      const dialog = within(screen.getByRole("dialog"));
      // Both the backdrop and the visible cancel button are accessible.
      await real.user.click(
        dialog.getAllByRole("button", { name: "Cancel" }).at(-1)!,
      );
      expect(real.outcome).toBe("cancel");
      real.view.unmount();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      Object.assign(model, {
        acknowledged: false,
        pending: false,
        refused: false,
      });
      real.view = render(
        <Dialog
          model={model}
          onConfirm={real.onConfirm}
          onCancel={real.onCancel}
        />,
      );
    }
    observe(model);
  }
  toString() {
    return `${this.step}(${this.value})`;
  }
}

it("requires fresh acknowledgment, blocks duplicate submission while pending and supports refusal and retry", async () => {
  const command = fc
    .tuple(
      fc.constantFrom<Step>(
        "acknowledge",
        "pending",
        "refused",
        "confirm",
        "cancelAndReopen",
      ),
      fc.boolean(),
    )
    .map(([step, value]) => new DialogCommand(step, value));
  const run = async (sequence: Iterable<fc.AsyncCommand<Model, Real>>) => {
    setActiveJurisdiction({ submissionPortal: "Sample portal" });
    try {
      await fc.asyncModelRun(() => {
        const model: Model = {
          acknowledged: false,
          pending: false,
          refused: false,
        };
        const real: Real = {
          view: render(<></>),
          user: userEvent.setup(),
          outcome: null,
          onConfirm: () => {
            real.outcome = "confirm";
          },
          onCancel: () => {
            real.outcome = "cancel";
          },
        };
        real.view.rerender(
          <Dialog
            model={model}
            onConfirm={real.onConfirm}
            onCancel={real.onCancel}
          />,
        );
        observe(model);
        return { model, real };
      }, sequence);
    } finally {
      cleanup();
      setActiveJurisdiction({});
    }
  };
  // A guaranteed full refusal/retry/cancellation path complements random order.
  await run([
    new DialogCommand("confirm", false),
    new DialogCommand("acknowledge", true),
    new DialogCommand("confirm", true),
    new DialogCommand("pending", true),
    new DialogCommand("confirm", true),
    new DialogCommand("pending", false),
    new DialogCommand("refused", true),
    new DialogCommand("confirm", true),
    new DialogCommand("cancelAndReopen", true),
    new DialogCommand("confirm", false),
  ]);
  await fc.assert(
    fc.asyncProperty(fc.commands([command], { maxCommands: 15 }), run),
    { numRuns: 20 },
  );
}, 20_000);
