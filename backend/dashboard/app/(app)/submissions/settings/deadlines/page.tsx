"use client";

/**
 * Agreed deadline extensions. Reporting dates always follow the regulator's
 * schedule; an override moves only a monthly return's due day, for example
 * when the supervisor has confirmed a different day for your bank.
 */

import { useEffect, useId, useState } from "react";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";
import { useBankContext } from "@/components/shell/BankContext";
import { SettingsHeading } from "@/components/submissions/settings/SettingsChrome";
import { DeniedSettings } from "@/components/submissions/settings/DeniedSettings";
import SectionCard from "@/components/ui/SectionCard";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  useReportingSettings,
  useReturnTemplates,
  useSaveReportingSettings,
} from "@/lib/api/hooks";
import { useConfigureEligibility, useViewEligibility } from "@/lib/api/configureAccess";

type Row = { returnCode: string; day: string };

const inputClass =
  "w-full min-h-10 rounded-sm border border-border bg-surface-raised px-2.5 py-2 text-body text-navy";

export default function DeadlinesPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const { allowed: canView, reason: viewReason } = useViewEligibility(bankId, "reg");
  const settingsQuery = useReportingSettings(bankId, { enabled: canView });
  const templatesQuery = useReturnTemplates(bankId);
  const save = useSaveReportingSettings(bankId);
  const { allowed: canConfigure, reason: configureReason } = useConfigureEligibility(
    bankId,
    "reg",
  );
  const reasonId = useId();
  const fieldId = useId();
  const [rows, setRows] = useState<Row[] | null>(null);

  const stored = settingsQuery.data?.deadlineOverrides;
  useEffect(() => {
    if (rows || !stored) return;
    setRows(
      Object.entries(stored).map(([returnCode, day]) => ({
        returnCode,
        day: String(day),
      })),
    );
  }, [stored, rows]);

  const monthly = (templatesQuery.data?.templates ?? []).filter(
    (template) => template.frequency === "monthly",
  );
  const invalid = (rows ?? []).some((row) => {
    const day = Number.parseInt(row.day, 10);
    return !row.returnCode || !Number.isInteger(day) || day < 1 || day > 31;
  });

  const handleSave = () => {
    if (!canConfigure || !rows || invalid) return;
    save.mutate(
      Object.fromEntries(rows.map((row) => [row.returnCode, Number.parseInt(row.day, 10)])),
    );
  };

  return (
    <>
      <SettingsHeading
        title="Deadlines"
        subtitle="Reporting dates and deadlines follow the regulator’s schedule for each return. Record an extension here only when your supervisor has agreed a different due day for your bank."
      />
      {!canView ? (
        <DeniedSettings title="Deadlines" reason={viewReason} />
      ) : settingsQuery.error ? (
        <ErrorPanel
          error={settingsQuery.error}
          onRetry={() => settingsQuery.refetch()}
          title="Could not load deadline extensions"
        />
      ) : !rows ? (
        <SkeletonCard />
      ) : (
        <SectionCard
          title="Agreed extensions"
          subtitle="Monthly returns only. The due day is clamped to the length of the month."
          actions={
            <span className="inline-flex max-w-64 flex-wrap items-center justify-end gap-2">
              {!canConfigure && (
                <span id={reasonId} className="text-right text-micro text-slate">
                  {configureReason}
                </span>
              )}
              <button
                type="button"
                disabled={!canConfigure || save.isPending || invalid}
                aria-describedby={!canConfigure ? reasonId : undefined}
                onClick={handleSave}
                className="inline-flex min-h-10 items-center gap-1.5 px-3 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {save.isPending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <Save size={13} aria-hidden />
                )}
                Save
              </button>
            </span>
          }
        >
          {rows.length === 0 ? (
            <p className="text-body text-slate">
              No extensions. Every return is due on the regulator’s schedule.
            </p>
          ) : (
            <ul className="max-w-2xl space-y-3">
              {rows.map((row, index) => (
                <li key={index} className="grid grid-cols-[minmax(0,1fr)_120px_auto] items-end gap-3">
                  <div>
                    <label className="mb-1.5 block text-caption font-medium text-navy" htmlFor={`${fieldId}-return-${index}`}>
                      Return
                    </label>
                    <select
                      id={`${fieldId}-return-${index}`}
                      value={row.returnCode}
                      onChange={(e) =>
                        setRows(rows.map((r, i) => (i === index ? { ...r, returnCode: e.target.value } : r)))
                      }
                      className={inputClass}
                    >
                      <option value="">Choose a return</option>
                      {monthly.map((template) => (
                        <option key={template.code} value={template.code}>
                          {template.code} · {template.title}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1.5 block text-caption font-medium text-navy" htmlFor={`${fieldId}-day-${index}`}>
                      Due day
                    </label>
                    <input
                      id={`${fieldId}-day-${index}`}
                      type="number"
                      min={1}
                      max={31}
                      value={row.day}
                      onChange={(e) =>
                        setRows(rows.map((r, i) => (i === index ? { ...r, day: e.target.value } : r)))
                      }
                      className={inputClass}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setRows(rows.filter((_, i) => i !== index))}
                    className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-sm border border-border text-slate hover:text-critical"
                    aria-label={`Remove the extension for ${row.returnCode || "this return"}`}
                  >
                    <Trash2 size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            onClick={() => setRows([...rows, { returnCode: "", day: "" }])}
            className="mt-4 inline-flex min-h-10 items-center gap-1.5 text-caption font-medium text-action hover:underline"
          >
            <Plus size={13} aria-hidden />
            Add an agreed extension
          </button>
          {invalid && rows.length > 0 && (
            <p className="mt-2 text-caption text-critical">
              Choose a return and a due day between 1 and 31 for every extension.
            </p>
          )}
          {save.error && (
            <div className="mt-4">
              <ErrorPanel error={save.error} title="Could not save" />
            </div>
          )}
          {save.isSuccess && (
            <p role="status" className="mt-4 text-caption font-medium text-success">
              Extensions saved. Due dates in the calendar now follow them.
            </p>
          )}
        </SectionCard>
      )}
    </>
  );
}
