"use client";

/**
 * A table the preparer fills in by hand.
 *
 * Two rules, both of which exist so a hand-entered figure cannot be mistaken
 * for a computed one:
 *
 *  - A BLANK CELL STAYS BLANK. It is never coerced to 0 — the platform cannot
 *    tell the difference between "no dividend was paid" and "nobody has
 *    entered the dividend yet", and neither can a reader looking at a zero.
 *  - Manual figures need an evidence attachment before the cycle is ready. The
 *    document the numbers were copied from is what makes them auditable.
 */

import { useMemo, useState } from "react";
import { isApiError } from "@/lib/api/client";
import {
  usePutIcaapManualTable,
  type IcaapAttachmentRead,
  type IcaapDataBlockRead,
  type IcaapManualColumn,
  type IcaapManualRow,
} from "@/lib/api/icaap";
import { INPUT_CLASS, PrimaryButton } from "./Dialog";

const MIN_REASON = 1;
const NUMERIC_KINDS = new Set(["amount", "ratio_pct", "count"]);

/** A blank stays blank; anything else must parse as a number. */
function cellError(kind: string, value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (!NUMERIC_KINDS.has(kind)) return null;
  return Number.isFinite(Number(trimmed)) ? null : "Enter a number, or leave it blank.";
}

type TableState = {
  columns: IcaapManualColumn[];
  rows: IcaapManualRow[];
};

function initialState(block: IcaapDataBlockRead): TableState {
  const payload = block.currentBinding?.payload as
    | { columns?: IcaapManualColumn[]; rows?: IcaapManualRow[] }
    | null
    | undefined;
  return {
    columns: payload?.columns ?? [],
    rows: payload?.rows ?? [],
  };
}

export default function ManualTableEditor({
  bankId,
  cycleId,
  block,
  attachments,
  readOnly,
}: {
  bankId: string;
  cycleId: string;
  block: IcaapDataBlockRead;
  attachments: readonly IcaapAttachmentRead[];
  readOnly: boolean;
}) {
  const [table, setTable] = useState<TableState>(() => initialState(block));
  const [evidenceId, setEvidenceId] = useState<string>(
    block.currentBinding?.evidenceAttachmentId ?? "",
  );
  const [reason, setReason] = useState("");
  const save = usePutIcaapManualTable(bankId, cycleId);

  const errors = useMemo(() => {
    const found: Record<string, string> = {};
    for (const row of table.rows) {
      for (const column of table.columns) {
        const message = cellError(
          column.kind,
          row.cells?.[column.key] ?? "",
        );
        if (message) found[`${row.key}:${column.key}`] = message;
      }
    }
    return found;
  }, [table]);

  const active = attachments.filter((attachment) => !attachment.withdrawn);

  if (table.columns.length === 0) {
    return (
      <p className="px-4 py-3 text-body text-slate">
        This table has no template yet. Refresh the block to load the rows the
        framework asks for.
      </p>
    );
  }

  const setCell = (rowKey: string, columnKey: string, value: string) => {
    setTable((current) => ({
      ...current,
      rows: current.rows.map((row) =>
        row.key === rowKey
          ? { ...row, cells: { ...row.cells, [columnKey]: value } }
          : row,
      ),
    }));
  };

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="overflow-x-auto">
        <table className="w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-left text-caption uppercase tracking-wider text-slate">
              <th className="py-2 pr-3 font-medium">Line</th>
              {table.columns.map((column) => (
                <th key={column.key} className="py-2 pr-3 font-medium">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => (
              <tr key={row.key} className="border-b border-border-light last:border-0">
                <td className="py-1.5 pr-3 text-ink">{row.label}</td>
                {table.columns.map((column) => {
                  const key = `${row.key}:${column.key}`;
                  return (
                    <td key={column.key} className="py-1.5 pr-3">
                      <input
                        className={`w-32 rounded border px-2 py-1 text-body tnum ${
                          errors[key] ? "border-critical" : "border-border"
                        } bg-surface-raised text-ink focus:border-action focus:outline-none`}
                        value={row.cells?.[column.key] ?? ""}
                        disabled={readOnly}
                        aria-label={`${row.label} ${column.label}`}
                        onChange={(event) =>
                          setCell(row.key, column.key, event.target.value)
                        }
                      />
                      {errors[key] && (
                        <span className="block text-caption text-critical">
                          {errors[key]}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-caption text-slate">
        Leave a cell blank where there is nothing to report. A blank is recorded
        as absent, never as zero.
      </p>

      {!readOnly && (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="text-caption font-medium text-navy">
              Evidence document
            </span>
            <select
              aria-label="Evidence document"
              className={INPUT_CLASS}
              value={evidenceId}
              onChange={(event) => setEvidenceId(event.target.value)}
            >
              <option value="">Not selected</option>
              {active.map((attachment) => (
                <option key={attachment.id} value={attachment.id}>
                  {attachment.title}
                </option>
              ))}
            </select>
          </label>
          <label className="block flex-1">
            <span className="text-caption font-medium text-navy">Reason</span>
            <input
              className={INPUT_CLASS}
              value={reason}
              maxLength={2000}
              placeholder="What changed, and where the figures came from"
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <PrimaryButton
            disabled={
              save.isPending ||
              Object.keys(errors).length > 0 ||
              reason.trim().length < MIN_REASON
            }
            onClick={() =>
              save.mutate({
                blockId: block.id,
                table: {
                  columns: table.columns,
                  rows: table.rows.map((row) => ({
                    ...row,
                    cells: Object.fromEntries(
                      Object.entries(row.cells ?? {}).map(([key, value]) => [
                        key,
                        value === null || String(value).trim() === ""
                          ? null
                          : String(value).trim(),
                      ]),
                    ),
                  })),
                  evidenceAttachmentId: evidenceId || null,
                  reason: reason.trim(),
                },
              })
            }
          >
            {save.isPending ? "Saving…" : "Save table"}
          </PrimaryButton>
        </div>
      )}

      {evidenceId === "" && (
        <p className="text-caption text-warning">
          Hand-entered figures need an evidence document before the cycle is
          ready to freeze.
        </p>
      )}
      {save.error != null && (
        <p className="text-body text-critical">
          {isApiError(save.error) ? save.error.message : "Could not save the table."}
        </p>
      )}
    </div>
  );
}
