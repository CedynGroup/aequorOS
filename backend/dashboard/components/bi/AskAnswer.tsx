"use client";

/**
 * The answer to a confirmed question.
 *
 * MISSING IS NEVER ZERO, and this component is the place that rule is kept for
 * this surface. An answer with no rows, or one whose every figure is absent, has
 * not measured zero — it has not measured. So it is stated in words, naming the
 * figures that were asked for and the date they were asked about, and NO table,
 * total or chart is drawn. A table of em dashes reads as a result; a chart of an
 * empty series draws a flat line on the baseline, which is a picture of a
 * measurement that never happened.
 *
 * Deliberately a table and not a chart. The answer to a question a reader has
 * just confirmed is a small set of rows they are checking against what they
 * asked; a chart would add a second thing to interpret, and the one drawing this
 * surface could plausibly want is exactly the one the absence rule forbids.
 * Formatting comes from `./result`, so a figure here reads the same as the same
 * figure in Explore, on a pack, and in the export the bank files beside it.
 */

import { Check, Database } from "lucide-react";
import type { BiQueryResult } from "@aequoros/risk-service-api";
import DataTable, { type Column } from "@/components/ui/DataTable";
import TrustBadge from "./TrustBadge";
import { formatCell, hasNoMeasuredValue, isEmptyResult } from "./result";
import {
  askEmptyAnswerSentence,
  askFigureText,
  type AskProposal,
} from "@/lib/api/ask";
import { fmtLocale } from "@/lib/format";

type Row = { cells: readonly unknown[] };

function localDay(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(fmtLocale(), {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export default function AskAnswer({
  proposal,
  result,
}: {
  proposal: AskProposal;
  result: BiQueryResult;
}) {
  const absent = isEmptyResult(result) || hasNoMeasuredValue(result);

  if (absent) {
    return (
      <section className="card flex items-start gap-3 p-5">
        <Database
          size={18}
          className="mt-0.5 shrink-0 text-slate"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-body font-medium text-navy">
            Your question ran. There is nothing to show.
          </p>
          <p className="mt-1 max-w-2xl text-body leading-relaxed text-navy/80">
            {askEmptyAnswerSentence(
              askFigureText(proposal.reading),
              localDay(proposal.asOf),
            )}
          </p>
        </div>
      </section>
    );
  }

  const columns: Column<Row>[] = result.columns.map((column, index) => ({
    key: `${column.id}-${index}`,
    header: column.label,
    numeric: column.kind === "measure",
    render: (row) => formatCell(row.cells[index], column.format),
  }));

  return (
    <section className="card overflow-hidden">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-light px-5 py-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-h3 text-navy">
            <Check size={16} className="shrink-0 text-compliant" aria-hidden />
            The question you confirmed
          </h2>
          <p className="mt-1 max-w-2xl text-caption leading-relaxed text-slate">
            {proposal.reading?.sentence ?? proposal.question}
          </p>
        </div>
        <TrustBadge
          status={result.trust?.status}
          failingChecks={result.trust?.failingChecks ?? []}
          size="compact"
        />
      </header>
      <DataTable
        columns={columns}
        rows={result.rows.map((cells) => ({ cells }))}
        density="compact"
        scrollLabel="Answer"
        className="border-0"
      />
      {result.truncated && (
        <p className="border-t border-border-light px-5 py-3 text-caption text-slate">
          Only the first {result.rows.length} rows are shown. Build the question
          in Explore to page through the rest or export it.
        </p>
      )}
    </section>
  );
}
