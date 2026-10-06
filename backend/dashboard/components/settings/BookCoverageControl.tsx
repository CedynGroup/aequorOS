"use client";

/**
 * The fifth dimension of the authority sentence: which part of one
 * institution's book the grant admits.
 *
 * Three choices, and the two narrowing ones are only offered when the bank has
 * declared something to narrow by. A bank that has not supplied a branch
 * register has no branches; a register with no `region` column has no regions
 * (it is declared, never inferred — see
 * `backend/app/domain/ingestion/reference_schemas/business_units.py`). In both
 * cases the control SAYS SO instead of opening a picker with nothing in it,
 * because an empty picker reads as a broken screen and sends an Org Owner
 * looking for a bug rather than to their data engineers.
 *
 * The kind is a scalar choice, like every other dimension. The value list is a
 * list because the stored scope is a list (`data_scope_values`), and it narrows
 * ONE binding — it never fans out into several. There is deliberately no
 * "select every branch": enumerating them all is not the same sentence as the
 * whole book, and it would fail the institution-ratio rule while looking
 * complete.
 */

import { RefreshCw } from "lucide-react";
import { useState } from "react";
import {
  BOOK_COVERAGE_OPTIONS,
  bookCoverageChoiceAvailable,
  coverageDirectory,
  type BookCoverageAvailability,
  type GrantDataScope,
  type GrantDataScopeKind,
} from "@/lib/api/grants";

type Choice = Readonly<{ value: string; label: string; detail: string | null }>;

function choicesFor(
  kind: GrantDataScopeKind,
  availability: BookCoverageAvailability,
): readonly Choice[] {
  const directory = coverageDirectory(availability);
  if (!directory) return [];
  if (kind === "region") {
    return directory.regions.map((region) => ({
      value: region,
      label: region,
      detail: null,
    }));
  }
  return directory.branches.map((branch) => ({
    value: branch.code,
    label: branch.name,
    detail: branch.region ? `${branch.code} · ${branch.region}` : branch.code,
  }));
}

export default function BookCoverageControl({
  availability,
  scope,
  refusal,
  disabled = false,
  onChange,
  onRetry,
}: {
  availability: BookCoverageAvailability;
  scope: GrantDataScope;
  /** Why this coverage cannot be granted yet, from `grantScopeRefusal`. */
  refusal: string | null;
  disabled?: boolean;
  onChange: (next: GrantDataScope) => void;
  onRetry?: () => void;
}) {
  const [filter, setFilter] = useState("");
  const note = "reason" in availability ? availability.reason : null;
  const loading = availability.status === "loading";
  const failed = availability.status === "unavailable";

  if (
    availability.status === "organization_wide" ||
    availability.status === "unsupported_module"
  ) {
    return (
      <section
        data-testid="book-coverage"
        className="rounded-md border border-border-light bg-surface p-4"
      >
        <p className="text-caption font-medium text-navy">Book coverage</p>
        <p className="mt-1.5 text-caption leading-relaxed text-slate">{note}</p>
      </section>
    );
  }

  const choices = choicesFor(
    scope.kind === "region" ? "region" : "branch",
    availability,
  );
  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? choices.filter(
        (choice) =>
          choice.label.toLowerCase().includes(needle) ||
          (choice.detail ?? "").toLowerCase().includes(needle),
      )
    : choices;
  const selected = new Set(scope.values);
  const noun = scope.kind === "region" ? "regions" : "branches";

  const toggle = (value: string) => {
    const next = new Set(selected);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange({ kind: scope.kind, values: [...next] });
  };

  return (
    <section
      data-testid="book-coverage"
      className="rounded-md border border-border-light bg-surface p-4"
    >
      <fieldset disabled={disabled}>
        <legend className="text-caption font-medium text-navy">
          Book coverage
        </legend>
        <div className="mt-2 space-y-1.5">
          {BOOK_COVERAGE_OPTIONS.map(([kind, label]) => {
            const available = bookCoverageChoiceAvailable(availability, kind);
            return (
              <label
                key={kind}
                className={`flex items-center gap-2 text-body ${
                  available ? "text-navy" : "text-slate opacity-65"
                }`}
              >
                <input
                  type="radio"
                  name="book-coverage"
                  value={kind}
                  checked={scope.kind === kind}
                  disabled={!available || loading}
                  onChange={() =>
                    onChange(
                      kind === "all"
                        ? { kind: "all", values: [] }
                        : { kind, values: [] },
                    )
                  }
                />
                {label}
              </label>
            );
          })}
        </div>

        {loading && (
          <p className="mt-3 text-caption text-slate">
            Reading this institution&apos;s branch register…
          </p>
        )}

        {note && (
          <p
            data-testid="book-coverage-note"
            className={`mt-3 rounded-md px-3 py-2 text-caption leading-relaxed ${
              failed
                ? "border border-warning/30 bg-warning-light/50 text-navy"
                : "text-slate"
            }`}
          >
            {note}
            {failed && onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="ml-2 inline-flex items-center gap-1 font-medium text-action hover:underline"
              >
                <RefreshCw size={12} aria-hidden /> Try again
              </button>
            )}
          </p>
        )}

        {scope.kind !== "all" && choices.length > 0 && (
          <div className="mt-3">
            {choices.length > 8 && (
              <input
                type="search"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder={`Search ${noun}`}
                aria-label={`Search ${noun}`}
                className="mb-2 w-full rounded-md border border-border bg-surface px-3 py-2 text-caption text-navy"
              />
            )}
            <ul className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-border-light p-2">
              {shown.map((choice) => (
                <li key={choice.value}>
                  <label className="flex items-start gap-2 rounded px-1 py-1 text-caption text-navy hover:bg-surface-muted">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={selected.has(choice.value)}
                      onChange={() => toggle(choice.value)}
                    />
                    <span className="min-w-0">
                      <span className="block truncate font-medium">
                        {choice.label}
                      </span>
                      {choice.detail && (
                        <span className="block truncate text-micro text-slate">
                          {choice.detail}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              ))}
              {shown.length === 0 && (
                <li className="px-1 py-1 text-caption text-slate">
                  Nothing matches that search.
                </li>
              )}
            </ul>
            <p className="mt-2 text-caption text-slate">
              {scope.values.length} of {choices.length} {noun} selected
            </p>
          </div>
        )}

        {refusal && (
          <p
            data-testid="book-coverage-refusal"
            role="alert"
            className="mt-3 text-caption leading-relaxed text-critical"
          >
            {refusal}
          </p>
        )}
      </fieldset>
    </section>
  );
}
