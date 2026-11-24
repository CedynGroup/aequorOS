// @vitest-environment jsdom
/**
 * Where the regulator has a filed return, read from the events the server writes.
 *
 * The live defect (2026-11-23): BoG accepted CAR-RWA v2 and "Check BoG status"
 * recorded it, yet the timeline stayed at "Received", because the screen read the
 * poll outcome from `detail.poll_status` while the server stores it under
 * `detail.result` (`workflow.check_regulator_status`). A second press on the
 * accepted return then met a 409, because the button stayed on offer after the
 * decision. These events are in the exact shape that check writes.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type {
  RegulatoryPackageSummaryRead,
  SubmissionEventRead,
} from "@aequoros/risk-service-api";
import FiledRecord, { regulatorStanding } from "../../components/submissions/FiledRecord";

afterEach(() => cleanup());

let clock = Date.UTC(2026, 10, 23, 22, 51, 21);

function event(name: string, detail: Record<string, unknown>): SubmissionEventRead {
  clock += 1000;
  return {
    id: `event-${clock}`,
    event: name,
    channel: "reporting_gateway",
    externalRef: "SRG-26-0000003",
    occurredAt: new Date(clock),
    detail,
  } as unknown as SubmissionEventRead;
}

const submitted = () => event("submitted", { gateway_status: "validating" });
const poll = (result: string, gateway: string) =>
  event("status_poll", { result, gateway_status: gateway });

it("reads an acceptance the status check recorded", () => {
  expect(
    regulatorStanding([
      submitted(),
      poll("acknowledged", "accepted"),
      event("acknowledged", { gateway_status: "accepted" }),
    ]),
  ).toBe("acknowledged");
});

it("reads a pending poll from the outcome the server stored", () => {
  expect(regulatorStanding([submitted(), poll("pending", "in_review")])).toBe("pending");
});

it("keeps a decision once recorded, whatever later polls say", () => {
  expect(
    regulatorStanding([
      submitted(),
      poll("rejected", "rejected"),
      event("rejected", { gateway_status: "rejected" }),
      poll("pending", "received"),
    ]),
  ).toBe("rejected");
});

it("has no standing before anything was asked", () => {
  expect(regulatorStanding([submitted()])).toBeNull();
});

function renderFiled(status: string, events: SubmissionEventRead[]) {
  const pkg = {
    id: "00000000-0000-0000-0000-000000000003",
    returnCode: "CAR-RWA",
    reportingDate: new Date("2026-06-30T00:00:00Z"),
    version: 2,
    status,
    isRehearsal: false,
    submissionRevision: "1.1",
  } as unknown as RegulatoryPackageSummaryRead;
  render(
    <FiledRecord
      pkg={pkg}
      events={events}
      chain={null}
      decisionLabels={{}}
      onCheckStatus={() => undefined}
      checking={false}
      checkError={null}
      lastCheckedLabel={null}
      onDownloadReceipt={null}
      correctionHref={null}
    />,
  );
}

const checkButton = () => screen.queryByTestId("check-regulator-status");

it("offers the status check while the regulator has not decided", () => {
  renderFiled("submitted", [submitted(), poll("pending", "in_review")]);
  expect(checkButton()).toBeInTheDocument();
});

it("offers no status check once the regulator accepted the return", () => {
  renderFiled("acknowledged", [
    submitted(),
    poll("acknowledged", "accepted"),
    event("acknowledged", { gateway_status: "accepted" }),
  ]);
  expect(checkButton()).not.toBeInTheDocument();
  expect(screen.getByText(/has accepted this return/)).toBeInTheDocument();
});
