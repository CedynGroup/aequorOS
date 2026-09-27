"use client";

/**
 * The classified loan blotter: every loan behind the credit metrics, graded
 * under the tenant's own classification grid, filtered and paged server-side.
 * URL is the source of truth for filters (the /positions pattern).
 *
 * It is also the landing page for a drill-through from a BI figure, which is why
 * every filter it offers is a filter the SERVER applies: sector, IFRS 9 stage,
 * days-past-due band and the reporting date arrive in the URL and go straight to
 * `/credit/loans`. Nothing is narrowed in the browser — a page that filtered
 * locally would report "matching filters" over one page instead of the book.
 *
 * A date the platform has computed no position for is refused by the server and
 * shown as a refusal here, never quietly replaced by the current book: a figure
 * measured on one date must not be explained with another date's loans.
 */

import PageContainer from "@/components/ui/PageContainer";
import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowUpRight,
  BookOpenCheck,
  CalendarX,
  ChevronLeft,
  ChevronRight,
  SearchX,
} from "lucide-react";
import type { Column } from "@/components/ui/DataTable";
import type { CreditLoanRead } from "@aequoros/risk-service-api";
import DataTable from "@/components/ui/DataTable";
import EmptyState from "@/components/ui/EmptyState";
import KpiStat from "@/components/ui/KpiStat";
import PageHeader from "@/components/ui/PageHeader";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { useBankContext } from "@/components/shell/BankContext";
import { utcDay } from "@/lib/api/biKeys";
import { isApiError, isModuleUnavailable } from "@/lib/api/client";
import { useCreditLoanFacets, useCreditLoansPage } from "@/lib/api/hooks";
import { fmtDateUTC, labelize, num } from "@/lib/api/values";
import { fmtCurrency, fmtInt } from "@/lib/format";

const PAGE_SIZES = [100, 250, 500];

/**
 * The days-past-due bands, in band order, as the platform defines them.
 *
 * The authority is `app/domain/credit/dpd_bands.py` — one definition shared by
 * the classification service, the migration engine and the BI fact row — and
 * these are its codes and labels, not a second banding. The server refuses a
 * code outside the list, so this control cannot offer one it would reject.
 */
const DPD_BANDS: ReadonlyArray<readonly [string, string]> = [
  ["current", "Current"],
  ["1_29", "1–29 days"],
  ["30_59", "30–59 days"],
  ["60_89", "60–89 days"],
  ["90_179", "90–179 days"],
  ["180_359", "180–359 days"],
  ["360_plus", "360+ days"],
];

/** The stages a canonical snapshot may state (1, 2 or 3 — nothing else). */
const IFRS9_STAGES: ReadonlyArray<readonly [string, string]> = [
  ["1", "Stage 1"],
  ["2", "Stage 2"],
  ["3", "Stage 3"],
];

function gradeTone(
  grade: string,
  nonPerforming: boolean,
): "success" | "amber" | "critical" {
  if (nonPerforming) return "critical";
  return grade === "olem" ? "amber" : "success";
}

/** The blotter columns, plus the lineage link when a date is in play. */
function loanColumns(asOf: string | undefined): Column<CreditLoanRead>[] {
  return [
    {
      key: "ref",
      header: "Reference",
      render: (r) => (
        <span className="font-mono text-caption text-navy">
          {r.sourceReference}
        </span>
      ),
    },
    {
      key: "borrower",
      header: "Borrower",
      render: (r) => (
        <span className="text-caption text-navy/85">
          {r.counterpartyName ?? "—"}
        </span>
      ),
    },
    {
      key: "product",
      header: "Product",
      render: (r) => (r.productCode ? labelize(r.productCode) : "—"),
    },
    {
      key: "sector",
      header: "Sector",
      render: (r) => r.sector ?? "—",
    },
    {
      key: "grade",
      header: "Grade",
      render: (r) => (
        <StatusPill tone={gradeTone(r.grade, r.nonPerforming)}>
          {labelize(r.grade)}
        </StatusPill>
      ),
    },
    {
      key: "stage",
      header: "IFRS 9 stage",
      render: (r) => (r.ifrs9Stage != null ? `Stage ${r.ifrs9Stage}` : "—"),
    },
    {
      key: "dpd",
      header: "DPD",
      align: "right",
      numeric: true,
      render: (r) =>
        r.daysPastDue != null ? (
          fmtInt(r.daysPastDue)
        ) : (
          <span title="Classified via the IFRS 9 stage proxy">—</span>
        ),
    },
    {
      key: "balance",
      header: "Outstanding",
      align: "right",
      numeric: true,
      render: (r) => fmtCurrency(num(r.exposureGhs)),
    },
    {
      key: "provision",
      header: "Provision required",
      align: "right",
      numeric: true,
      render: (r) => fmtCurrency(num(r.provisionRequiredGhs)),
    },
    {
      key: "held",
      header: "Provision held",
      align: "right",
      numeric: true,
      render: (r) =>
        r.provisionHeldGhs != null ? fmtCurrency(num(r.provisionHeldGhs)) : "—",
    },
    {
      key: "branch",
      header: "Branch",
      render: (r) => r.branchId ?? "—",
    },
    {
      // A loan IS a canonical position, so its lineage is the position
      // blotter's — one drawer, not a second copy of it here.
      key: "lineage",
      header: "Lineage",
      align: "right",
      render: (r) => (
        <Link
          href={`/positions?ref=${encodeURIComponent(r.sourceReference)}${
            asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""
          }`}
          aria-label={`Source and lineage for ${r.sourceReference}`}
          className="inline-flex items-center gap-1 whitespace-nowrap text-caption font-medium text-action hover:underline"
        >
          Source
          <ArrowUpRight size={12} aria-hidden />
        </Link>
      ),
    },
  ];
}

function LoanBookBody() {
  const router = useRouter();
  const params = useSearchParams();
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const grade = params.get("grade") ?? undefined;
  const product = params.get("product") ?? undefined;
  const branch = params.get("branch") ?? undefined;
  const sector = params.get("sector") ?? undefined;
  const stageParam = params.get("stage") ?? "";
  const dpdBand = params.get("dpd_band") ?? undefined;
  const asOfParam = params.get("as_of") ?? undefined;
  const q = params.get("q") ?? "";
  const limit = Number(params.get("limit") ?? PAGE_SIZES[0]);
  const offset = Number(params.get("offset") ?? 0);

  // A stage outside the platform's three is not sent: the server would refuse
  // it, and refusing a hand-edited URL parameter here keeps the page readable.
  const stage = IFRS9_STAGES.some(([code]) => code === stageParam)
    ? Number(stageParam)
    : undefined;

  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    const handle = setTimeout(() => {
      if (search !== q) setParam("q", search || null, { resetOffset: true });
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function setParam(
    key: string,
    value: string | null,
    opts?: { resetOffset?: boolean },
  ) {
    const next = new URLSearchParams(params.toString());
    if (value === null || value === "") next.delete(key);
    else next.set(key, value);
    if (opts?.resetOffset) next.delete("offset");
    router.replace(`/credit/book?${next.toString()}`, { scroll: false });
  }

  function clearFilters() {
    router.replace("/credit/book", { scroll: false });
  }

  const page = useCreditLoansPage(bankId, {
    limit,
    offset,
    grade,
    product,
    branch,
    sector,
    stage,
    dpdBand,
    asOf: asOfParam,
    q,
  });
  const facets = useCreditLoanFacets(bankId);
  const rows = useMemo(() => page.data?.rows ?? [], [page.data]);
  const total = page.data?.total ?? 0;
  const filtered = page.data?.filtered ?? 0;
  const columns = useMemo(
    () => loanColumns(page.data?.asOf),
    [page.data?.asOf],
  );

  const filtersActive = Boolean(
    grade || product || branch || sector || stage || dpdBand || asOfParam || q,
  );

  // The server refuses a date it has computed no position for, and a date whose
  // snapshot holds no loans answers `no_loan_book`. Both are about the DATE, so
  // both say so and offer the current book as a CHOICE — never as a silent
  // substitution, and never as a dead end the reader can only leave by editing
  // the URL.
  const dateRefused =
    Boolean(asOfParam) &&
    ((isApiError(page.error) &&
      page.error.errorCode === "no_computed_position") ||
      (isModuleUnavailable(page.error) &&
        page.error.errorCode === "no_loan_book"));

  const selectClass =
    "px-2.5 py-2 text-caption font-medium bg-surface-raised border border-border rounded-md text-navy";
  const pagerButtonClass =
    "inline-flex items-center gap-1 px-2.5 py-1.5 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40 disabled:pointer-events-none";

  const windowStart = filtered === 0 ? 0 : offset + 1;
  const windowEnd = Math.min(offset + rows.length, filtered);

  if (dateRefused) {
    return (
      <>
        <PageHeader eyebrow="Credit" title="Loan Book" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={CalendarX}
            title={
              asOfParam
                ? `No loan book as of ${fmtDateUTC(utcDay(asOfParam))}`
                : "No loan book for that date"
            }
            description={
              page.error instanceof Error ? page.error.message : undefined
            }
            action={
              <button
                type="button"
                onClick={clearFilters}
                className="btn-primary"
              >
                Show the current book
              </button>
            }
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <PageHeader eyebrow="Credit" title="Loan Book" asOf={page.data?.asOf} />
      <QueryBoundary
        isLoading={page.isLoading}
        error={page.error}
        onRetry={() => page.refetch()}
      >
        {page.data && total === 0 ? (
          <PageContainer className="py-6">
            <EmptyState
              Icon={BookOpenCheck}
              title="No loans in the canonical book yet"
              description="Ingest the loan book through the Data Engine to populate the classified blotter."
              action={
                <a href="/data-engine" className="btn-primary">
                  Open the Data Engine
                </a>
              }
            />
          </PageContainer>
        ) : page.data ? (
          <PageContainer className="py-6 space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiStat
                label="Loans on book"
                value={fmtInt(total)}
                hint="Current generation"
              />
              <KpiStat
                label="Matching filters"
                value={fmtInt(filtered)}
                hint={
                  filtered === total ? "No filters applied" : "Server-filtered"
                }
              />
              <KpiStat
                label="Non-performing rows"
                value={fmtInt(rows.filter((r) => r.nonPerforming).length)}
                hint="On this page"
              />
              <KpiStat
                label="Page exposure"
                value={fmtCurrency(
                  rows.reduce((sum, r) => sum + num(r.exposureGhs), 0),
                )}
                hint="Sum of the visible rows"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search reference or borrower…"
                aria-label="Search by reference or borrower"
                className={`${selectClass} w-64`}
              />
              <select
                className={selectClass}
                aria-label="Filter by classification grade"
                value={grade ?? ""}
                onChange={(e) =>
                  setParam("grade", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">All grades</option>
                {(facets.data?.grades ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {labelize(f.value)} ({f.count})
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                aria-label="Filter by product"
                value={product ?? ""}
                onChange={(e) =>
                  setParam("product", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">All products</option>
                {(facets.data?.products ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {labelize(f.value)} ({f.count})
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                aria-label="Filter by branch"
                value={branch ?? ""}
                onChange={(e) =>
                  setParam("branch", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">All branches</option>
                {(facets.data?.branches ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.value} ({f.count})
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                aria-label="Filter by sector"
                value={sector ?? ""}
                onChange={(e) =>
                  setParam("sector", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">All sectors</option>
                {(facets.data?.sectors ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.value} ({f.count})
                  </option>
                ))}
                {/* A sector carried in from a figure that this book does not
                    state stays visible, so the heading keeps matching the URL. */}
                {sector &&
                  !(facets.data?.sectors ?? []).some(
                    (f) => f.value === sector,
                  ) && <option value={sector}>{sector}</option>}
              </select>
              <select
                className={selectClass}
                aria-label="Filter by IFRS 9 stage"
                value={stage ? String(stage) : ""}
                onChange={(e) =>
                  setParam("stage", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">All stages</option>
                {IFRS9_STAGES.map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                aria-label="Filter by days past due"
                value={dpdBand ?? ""}
                onChange={(e) =>
                  setParam("dpd_band", e.target.value || null, {
                    resetOffset: true,
                  })
                }
              >
                <option value="">Any days past due</option>
                {DPD_BANDS.map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
              {asOfParam && (
                <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-2 text-caption font-medium text-slate">
                  As of {fmtDateUTC(utcDay(asOfParam))}
                  <button
                    type="button"
                    onClick={() =>
                      setParam("as_of", null, { resetOffset: true })
                    }
                    className="text-action hover:underline"
                  >
                    Current book
                  </button>
                </span>
              )}
              {filtersActive && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-caption font-medium text-action hover:underline"
                >
                  Clear filters
                </button>
              )}
            </div>

            {filtered === 0 ? (
              <EmptyState
                Icon={SearchX}
                title="No loans match these filters"
                description="The book has loans, but none in this slice. Widen a filter or clear them to see the whole book."
                action={
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="btn-primary"
                  >
                    Clear filters
                  </button>
                }
              />
            ) : (
              <SectionCard title="Classified loans" noPadding>
                <div
                  className={page.isFetching ? "opacity-50" : ""}
                  aria-busy={page.isFetching}
                >
                  <DataTable
                    columns={columns}
                    rows={rows}
                    density="compact"
                    stickyHeader
                    maxHeight="62vh"
                  />
                </div>
              </SectionCard>
            )}

            <div
              className={`flex items-center justify-between ${filtered === 0 ? "hidden" : ""}`}
            >
              <p className="text-caption text-slate">
                Showing {fmtInt(windowStart)}–{fmtInt(windowEnd)} of{" "}
                {fmtInt(filtered)} loans
              </p>
              <div className="flex items-center gap-2">
                <select
                  className={selectClass}
                  aria-label="Rows per page"
                  value={String(limit)}
                  onChange={(e) =>
                    setParam("limit", e.target.value, { resetOffset: true })
                  }
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size} rows
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={pagerButtonClass}
                  disabled={offset === 0}
                  onClick={() =>
                    setParam("offset", String(Math.max(0, offset - limit)))
                  }
                >
                  <ChevronLeft size={14} /> Prev
                </button>
                <button
                  type="button"
                  className={pagerButtonClass}
                  disabled={offset + limit >= filtered}
                  onClick={() => setParam("offset", String(offset + limit))}
                >
                  Next <ChevronRight size={14} />
                </button>
              </div>
            </div>
          </PageContainer>
        ) : null}
      </QueryBoundary>
    </>
  );
}

export default function CreditLoanBookPage() {
  return (
    <Suspense>
      <LoanBookBody />
    </Suspense>
  );
}
