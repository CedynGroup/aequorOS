"use client";

import { useState } from "react";
import type {
  CapitalFloorRead,
  CapitalPlanProjectionReadCet1Min,
  CapitalPlanProjectionScenario,
  CapitalPlanProjectionYear,
  CapitalPlanSummaryReadProjection,
  CapitalPlanSummaryReadProjectionUnavailable,
} from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import SubTabs from "@/components/ui/SubTabs";
import DataTable, { type Column } from "@/components/ui/DataTable";
import { SCENARIO_LABELS } from "@/components/forecasting/lib";
import { fmtDateUTC, fmtFloorPct, labelize, numOrNull } from "@/lib/api/values";
import { fmtPct } from "@/lib/format";

/**
 * The capital plan's own projection: each stored 5-year forecast scenario's
 * CAR, Tier 1 and CET1 path, measured against the minima the plan is held to.
 *
 * Every minimum is the one the backend resolved — the governed regulatory
 * floor, or the institution's stricter register value, at the projection's
 * as-of date — and the card says which, and from which instrument. Nothing
 * here is a literal: no floor is assumed when the contract carries none, and a
 * ratio the forecast did not project reads "Not projected", never 0.
 */

type FloorLike = CapitalFloorRead | CapitalPlanProjectionReadCet1Min;

function floorValue(floor: FloorLike | null | undefined): number | null {
  return floor ? numOrNull(floor.valuePct) : null;
}

/** "+1.25 pp" / "−0.40 pp" — headroom is in percentage points, not percent. */
function fmtPp(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(2)} pp`;
}

function ratio(value: string | null | undefined): string {
  const parsed = numOrNull(value);
  return parsed === null ? "Not projected" : fmtPct(parsed, 2);
}

function Headroom({ value }: { value: string | null | undefined }) {
  const parsed = numOrNull(value);
  const tone =
    parsed === null ? "text-slate" : parsed < 0 ? "text-critical" : "text-navy";
  return <span className={tone}>{fmtPp(parsed)}</span>;
}

/** What the minimum is measured against, in words (CCB = conservation buffer). */
function bufferPhrase(floor: FloorLike): string | null {
  if (floor.conservationBuffer === "included") {
    return "includes the capital conservation buffer";
  }
  if (floor.conservationBuffer === "excluded") {
    return "excludes the capital conservation buffer";
  }
  return null;
}

/** One sentence on where a minimum came from. The parameter record's date is
 *  not shown: it is the platform record's, not the instrument's commencement. */
function floorSource(floor: FloorLike): string {
  const value = numOrNull(floor.valuePct);
  const shown = value === null ? floor.valuePct : fmtFloorPct(value);
  const parts: string[] = [];
  const regulatory = numOrNull(floor.regulatoryValuePct ?? null);
  if (floor.source === "board_register" && regulatory === null) {
    parts.push(
      `${shown} — the institution's own minimum; no regulatory value is configured for it`,
    );
  } else if (floor.source === "board_register") {
    parts.push(
      `${shown} — the institution's own minimum, stricter than the regulatory ${fmtFloorPct(regulatory ?? 0)}`,
    );
  } else if (floor.raisedToRegulatoryFloor) {
    const board = numOrNull(floor.boardRegisterPct ?? null);
    parts.push(
      `${shown} — the regulatory minimum; the institution's register value${
        board === null ? "" : ` of ${fmtFloorPct(board)}`
      } is below it and is raised to it`,
    );
  } else {
    parts.push(`${shown} — the regulatory minimum`);
  }
  const buffer = bufferPhrase(floor);
  if (buffer) parts.push(buffer);
  if (floor.sourceCitation) parts.push(floor.sourceCitation);
  const status = confirmationPhrase(floor);
  if (status) parts.push(status);
  return parts.join(" · ");
}

/** "pending confirmation" when the governed value awaits confirmation. The
 *  value still applies; the card says it is provisional rather than hiding it. */
function confirmationPhrase(
  floor: FloorLike | null | undefined,
): string | null {
  const status = floor?.confirmationStatus;
  if (!status || status === "confirmed") return null;
  return status === "pending"
    ? "pending confirmation"
    : `${labelize(status).toLowerCase()} confirmation`;
}

function PendingNote({ floor }: { floor: FloorLike | null | undefined }) {
  const phrase = confirmationPhrase(floor);
  if (!phrase) return null;
  return (
    <p className="text-caption text-slate">
      {phrase.charAt(0).toUpperCase() + phrase.slice(1)}
    </p>
  );
}

function buildColumns(basel: boolean): Column<CapitalPlanProjectionYear>[] {
  const columns: Column<CapitalPlanProjectionYear>[] = [
    {
      key: "period",
      header: "Year",
      render: (row) => row.periodLabel,
      width: "22%",
    },
    {
      key: "car",
      header: "CAR",
      numeric: true,
      render: (row) => ratio(row.carPct),
    },
    {
      key: "car-headroom",
      header: "Headroom to total requirement (incl. buffer)",
      numeric: true,
      render: (row) => <Headroom value={row.headroomPp} />,
    },
  ];
  if (!basel) return columns;
  return [
    ...columns,
    {
      key: "tier1",
      header: "Tier 1",
      numeric: true,
      render: (row) => ratio(row.tier1Pct),
    },
    {
      key: "tier1-headroom",
      header: "Headroom to Tier 1 minimum (excl. buffer)",
      numeric: true,
      render: (row) => <Headroom value={row.tier1HeadroomPp} />,
    },
    {
      key: "cet1",
      header: "CET1",
      numeric: true,
      render: (row) => ratio(row.cet1Pct),
    },
    {
      key: "cet1-headroom",
      header: "Headroom to CET1 minimum (excl. buffer)",
      numeric: true,
      render: (row) => <Headroom value={row.cet1HeadroomPp} />,
    },
  ];
}

function scenarioLabel(code: string): string {
  return `${SCENARIO_LABELS[code] ?? labelize(code)} scenario`;
}

export default function CapitalPlanProjection({
  projection,
}: {
  projection: CapitalPlanSummaryReadProjection;
}) {
  const scenarios: CapitalPlanProjectionScenario[] = projection.scenarios;
  const [choice, setChoice] = useState<string | null>(null);
  const active =
    scenarios.find((scenario) => scenario.scenarioCode === choice) ??
    scenarios[0];

  const pillar1 = floorValue(projection.pillar1Min);
  const pillar2 = numOrNull(projection.pillar2AddonPct);
  const total = numOrNull(projection.totalRequirementPct);
  const tier1 = floorValue(projection.tier1Min);
  const cet1 = floorValue(projection.cet1Min);
  // The Basel sub-tier ratios exist only under the bank capital regime; the
  // backend says which regime applies, so an unconfigured Tier 1 / CET1 floor
  // never hides the ratios themselves.
  const basel = projection.baselRatiosApplicable !== false;

  return (
    <SectionCard
      title="Capital plan projection"
      subtitle={`Stored five-year forecast runs measured against the plan's requirement, as of ${fmtDateUTC(
        projection.asOfDate,
      )}${projection.yearEndAligned ? " (financial year-end)" : ""}`}
      footer={
        active ? (
          <span>
            Figures from the stored {scenarioLabel(active.scenarioCode)}{" "}
            forecast run; headroom is in percentage points.
          </span>
        ) : undefined
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-3">
          <div>
            <p className="text-caption text-slate">Total capital requirement</p>
            <p className="mt-1 text-body font-medium text-navy">
              {total === null ? "—" : fmtFloorPct(total)}
            </p>
            <p className="text-caption text-slate">
              Pillar 1 minimum {pillar1 === null ? "—" : fmtFloorPct(pillar1)} +
              Pillar 2 add-ons {pillar2 === null ? "—" : fmtFloorPct(pillar2)}
            </p>
            <PendingNote floor={projection.pillar1Min} />
          </div>
          {basel ? (
            <>
              <div>
                <p className="text-caption text-slate">Tier 1 minimum</p>
                <p className="mt-1 text-body font-medium text-navy">
                  {tier1 === null ? "Not configured" : fmtFloorPct(tier1)}
                </p>
                <PendingNote floor={projection.tier1Min} />
              </div>
              <div>
                <p className="text-caption text-slate">CET1 minimum</p>
                <p className="mt-1 text-body font-medium text-navy">
                  {cet1 === null ? "Not configured" : fmtFloorPct(cet1)}
                </p>
                <PendingNote floor={projection.cet1Min} />
              </div>
            </>
          ) : null}
        </div>

        {basel ? (
          <p className="text-caption text-slate">
            The CET1 and Tier 1 minima exclude the capital conservation buffer,
            which is held in CET1 on top of them and is included in the total
            capital requirement. Headroom to the CET1 and Tier 1 minima
            therefore does not show whether the buffer is met; buffer compliance
            is not assessed here.
          </p>
        ) : null}

        <ul className="space-y-1 text-caption text-slate">
          <li>Pillar 1 minimum: {floorSource(projection.pillar1Min)}</li>
          {projection.tier1Min ? (
            <li>Tier 1 minimum: {floorSource(projection.tier1Min)}</li>
          ) : null}
          {projection.cet1Min ? (
            <li>CET1 minimum: {floorSource(projection.cet1Min)}</li>
          ) : null}
        </ul>

        {scenarios.length > 1 ? (
          <SubTabs
            items={scenarios.map((scenario) => ({
              key: scenario.scenarioCode,
              label: scenarioLabel(scenario.scenarioCode),
            }))}
            active={active?.scenarioCode ?? ""}
            onChange={setChoice}
          />
        ) : null}

        {active ? (
          <DataTable
            columns={buildColumns(basel)}
            rows={active.years}
            density="compact"
            scrollLabel="Capital plan projection"
          />
        ) : (
          <p className="text-body text-slate">
            No forecast scenario is available to project.
          </p>
        )}
      </div>
    </SectionCard>
  );
}

/** Stands in for the projection card when forecast runs exist but no capital
 *  minimum can be resolved — the plan, its approval state and the ILAAP
 *  evidence stay usable; only the projection says why it is absent. */
export function CapitalPlanProjectionUnavailable({
  unavailable,
}: {
  unavailable: CapitalPlanSummaryReadProjectionUnavailable;
}) {
  return (
    <SectionCard
      title="Capital plan projection"
      subtitle="The projection cannot be measured for this institution"
    >
      <p className="text-body text-slate">{unavailable.reason}</p>
    </SectionCard>
  );
}
