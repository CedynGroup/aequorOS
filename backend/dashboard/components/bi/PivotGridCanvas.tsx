"use client";

/**
 * The record grid over `POST …/bi/grid`, on AG Grid COMMUNITY.
 *
 * WHAT THIS COMPONENT DOES NOT DO. It does no analytics. Grouping, subtotals,
 * pivoting, Top-N and period comparison are compiled server-side by
 * `app/services/bi/compiler.py` — they always were — and arrive as an ordinary
 * rectangle of keyed rows with a level marker lifted onto each row. The
 * Community edition has no server-side row model and no row-grouping API, so
 * what is left for the library is exactly what a grid should do: draw cells,
 * scroll, and ask for the next page. A grouped answer is drawn as indented rows
 * with the server's own subtotals, never re-aggregated here.
 *
 * WHY THE INFINITE ROW MODEL. A fact grain can be millions of rows. The
 * Community client-side model would have to hold all of them, and the server
 * caps a page anyway — so the grid pages through the endpoint. The page size is
 * NOT a number chosen here: the first request omits `end_row`, which asks the
 * server for one page at ITS cap, and the size of that page is the cap the
 * surface enforces. If the cap is changed in `BI_GRID_PAGE_CAP` the grid follows
 * it without an edit.
 *
 * THE BUILT-IN EXPORT IS OFF, IN TWO INDEPENDENT WAYS. AG Grid's own CSV export
 * would copy rows out of the browser with no authorization decision, no audit
 * record and no watermark, which is a disclosure path — the governed Export menu
 * is the only way out of a BI view. So `CsvExportModule` is never registered
 * (without it the export API does not exist at all) AND `suppressCsvExport` /
 * `suppressExcelExport` are set. `pivotGrid.test.ts` proves both, and proves
 * that nothing under `components/bi/` calls an export API.
 *
 * TRUNCATION IS VISIBLE. A page that hit the cap says so, in the notice above
 * the grid. A silently truncated grid is a wrong answer.
 *
 * Imported ONLY through `./PivotGrid`, which loads it with
 * `dynamic(..., { ssr: false })`: AG Grid reaches for the DOM and is a large
 * runtime that `scripts/assert-home-route-bundle.mjs` keeps out of the Command
 * Center's bundle.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  CellStyleModule,
  InfiniteRowModelModule,
  ModuleRegistry,
  RowStyleModule,
  TooltipModule,
  ValidationModule,
  themeQuartz,
  type ColDef,
  type ICellRendererParams,
  type IDatasource,
  type IGetRowsParams,
  type SortModelItem,
  type ValueGetterParams,
} from "ag-grid-community";
import type {
  BiGridPageRead,
  BiGridRowRead,
  BiQuery,
  BiResultColumn,
  BiSort,
} from "@aequoros/risk-service-api";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import {
  biRefusalSentence,
  isBiAccessDenied,
  useBiGrid,
  useBiGridPages,
} from "@/lib/api/bi";
import type { ReaderCoverage } from "@/lib/api/dataScope";
import NeedsDataWidget from "./NeedsDataWidget";
import RefusedWidget from "./RefusedWidget";
import RestrictedWidget from "./RestrictedWidget";
import TrustBadge from "./TrustBadge";
import { useChartTheme } from "./echartsTheme";
import { legalSorts } from "./exploreQuery";
import {
  columnUnit,
  formatCell,
  isNumericFormat,
  subtotalLabel,
  truncationNotice,
} from "./gridCells";
import { NOT_MEASURED } from "./result";
import type { BiDatasetRequirement } from "./types";

/**
 * Exactly the Community modules this grid uses, and no export module.
 *
 * AG Grid v33 onwards requires each feature's module to be registered. The
 * convenience bundle `AllCommunityModule` is deliberately NOT used: it includes
 * `CsvExportModule`, which would put `exportDataAsCsv` back on the grid API and
 * reopen the ungoverned way out of a BI view.
 *
 * `ValidationModule` turns AG Grid's terse production error codes into readable
 * messages and is registered outside production only, which is what its own
 * documentation asks for.
 */
ModuleRegistry.registerModules([
  InfiniteRowModelModule,
  CellStyleModule,
  RowStyleModule,
  TooltipModule,
  ...(process.env.NODE_ENV === "production" ? [] : [ValidationModule]),
]);

/** Indent, in pixels, per level of a rolled-up answer. */
const INDENT_PER_LEVEL = 14;
/** How many pages the grid keeps; beyond this the oldest are re-fetched. */
const PAGES_KEPT = 4;

/**
 * One row as the grid holds it: the server's cells under `values`, keyed by
 * column id, with the roll-up metadata beside them rather than mixed in. A
 * column id and a metadata key can therefore never collide.
 */
type GridRowData = Readonly<{
  values: Readonly<Record<string, unknown>>;
  level: number | null;
  subtotal: boolean;
}>;

function rowsOf(page: BiGridPageRead): GridRowData[] {
  return page.rows.map((row: BiGridRowRead) => ({
    values: row.values,
    level: row.level ?? null,
    subtotal: row.subtotal === true,
  }));
}

function headerName(column: BiResultColumn): string {
  const unit = columnUnit(column.format);
  return unit === null ? column.label : `${column.label} (${unit})`;
}

/** The first row field's cell: indented, and named for what the row IS. */
function RowHeadCell(
  params: ICellRendererParams & {
    dimensionColumns: readonly BiResultColumn[];
    dimensionCount: number;
  },
) {
  const data = params.data as GridRowData | undefined;
  if (!data) return null;
  const depth =
    data.subtotal && data.level !== null ? data.level : params.dimensionCount;
  const indent = Math.max(0, depth - 1) * INDENT_PER_LEVEL;

  let text = params.valueFormatted ?? "";
  if (data.subtotal) {
    const deepest = [...params.dimensionColumns]
      .slice(0, Math.max(0, data.level ?? 0))
      .reverse()
      .map((column) =>
        formatCell(data.values[column.id], column.format),
      )
      .find((value) => value !== "" && value !== NOT_MEASURED);
    text = subtotalLabel(data.level ?? 0, deepest ?? "");
  }

  return (
    <span
      style={{ paddingLeft: indent }}
      className={data.subtotal ? "font-semibold text-navy" : undefined}
    >
      {text}
    </span>
  );
}

function columnDefsFor(page: BiGridPageRead): ColDef<GridRowData>[] {
  const dimensionColumns = page.columns.filter(
    (column) => column.kind === "dimension",
  );
  const pivoted = page.columns.some(
    (column) => (column.pivotValue ?? null) !== null,
  );
  let dimensionsSeen = 0;

  return page.columns.map((column) => {
    const isDimension = column.kind === "dimension";
    if (isDimension) dimensionsSeen += 1;
    const isFirstDimension = isDimension && dimensionsSeen === 1;
    const numeric = isNumericFormat(column.format);

    const definition: ColDef<GridRowData> = {
      colId: column.id,
      headerName: headerName(column),
      headerTooltip: column.label,
      valueGetter: (params: ValueGetterParams<GridRowData>) =>
        params.data === undefined ? null : (params.data.values[column.id] ?? null),
      valueFormatter: (params) => formatCell(params.value, column.format),
      // Two reasons a measure column does not offer a sort. A pivoted answer is
      // ordered by its row fields — the compiler refuses to order one by a
      // measure — and a COMPARISON column (prior, change, change %) carries its
      // base measure's member id, so ordering by it would silently order by the
      // current value instead. A header click that cannot do what it says is
      // worse than a header that does not offer it.
      sortable:
        isDimension || (!pivoted && (column.role ?? "current") === "current"),
      minWidth: isDimension ? 170 : 150,
      flex: isDimension ? 1 : 0,
      width: numeric ? 160 : undefined,
      cellClass: numeric ? "ag-right-aligned-cell" : undefined,
      headerClass: numeric ? "ag-right-aligned-header" : undefined,
    };

    if (isFirstDimension) {
      definition.cellRenderer = RowHeadCell;
      definition.cellRendererParams = {
        dimensionColumns,
        dimensionCount: page.dimensionCount,
      };
      definition.minWidth = 240;
    }
    return definition;
  });
}

/** The column id → catalogue member map a sort has to be expressed in. */
function memberOfColumn(page: BiGridPageRead, colId: string): string | null {
  const column = page.columns.find((entry) => entry.id === colId);
  return column?.memberId ?? null;
}

function sortsFrom(
  page: BiGridPageRead,
  model: readonly SortModelItem[],
  query: BiQuery,
): Readonly<{ sort: BiSort[]; droppedOverCap: number }> {
  const requested: BiSort[] = [];
  for (const entry of model) {
    const member = memberOfColumn(page, entry.colId);
    if (member === null) continue;
    requested.push({ member, direction: entry.sort });
  }
  const resolved = legalSorts(requested, {
    measures: query.measures,
    dimensions: query.dimensions ?? [],
    pivoted: (query.pivot ?? null) !== null,
  });
  return { sort: resolved.sort, droppedOverCap: resolved.droppedOverCap };
}

export type PivotGridCanvasProps = {
  bankId: string | undefined;
  /** The question. It carries no paging of its own — the grid supplies that. */
  query: BiQuery | null;
  /**
   * The dataset the question's author NAMED as the one it degrades without, or
   * null when none was named — in which case the empty state says it does not
   * know why, rather than sending the reader to a template it cannot vouch for.
   */
  dataset: BiDatasetRequirement | null;
  /** How much of the book the reader's access covers, when the caller knows. */
  coverage?: ReaderCoverage;
  /** Grid viewport height in pixels. */
  height?: number;
};

export default function PivotGridCanvas({
  bankId,
  query,
  dataset,
  coverage,
  height = 520,
}: PivotGridCanvasProps) {
  const fetchPage = useBiGridPages(bankId);

  // The opening page, asked for WITHOUT an end row: the server answers with one
  // page at its own cap, so its length is the page size the grid then uses.
  const probe = useBiGrid(bankId, query === null ? null : { query, startRow: 0 });
  const page = probe.data;

  const [pagingError, setPagingError] = useState<string | null>(null);
  const [sortNotice, setSortNotice] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    setPagingError(null);
    setSortNotice(null);
    setTruncated(page?.truncated === true);
  }, [page]);

  const pageSize = useMemo(() => {
    if (page === undefined) return null;
    // A page that was not truncated is the whole answer, so its own length is
    // the only block the grid will ever ask for.
    return Math.max(1, page.rows.length);
  }, [page]);

  const columnDefs = useMemo(
    () => (page === undefined ? [] : columnDefsFor(page)),
    [page],
  );

  const theme = useAgGridTheme();

  const datasource = useMemo<IDatasource | undefined>(() => {
    if (page === undefined || query === null || pageSize === null) {
      return undefined;
    }
    return {
      getRows: (params: IGetRowsParams) => {
        const sorted = sortsFrom(page, params.sortModel, query);
        setSortNotice(
          sorted.droppedOverCap > 0
            ? "This answer is ordered by the first columns you chose; the later ones were set aside."
            : null,
        );
        const unsorted = params.sortModel.length === 0;
        if (params.startRow === 0 && unsorted) {
          // The opening page, already fetched and already authorized. Asking
          // again would be a second query log entry for one answer, and its
          // truncation is already on screen from the effect above.
          params.successCallback(rowsOf(page), page.lastRow ?? undefined);
          return;
        }
        void fetchPage({
          query: { ...query, sort: sorted.sort },
          startRow: params.startRow,
          endRow: params.endRow,
        })
          .then((next) => {
            params.successCallback(rowsOf(next), next.lastRow ?? undefined);
            setTruncated(next.truncated);
            setPagingError(null);
          })
          .catch(() => {
            params.failCallback();
            setPagingError(
              "The next rows could not be loaded. Scroll back and try again, or narrow the question.",
            );
          });
      },
    };
  }, [fetchPage, page, pageSize, query]);

  if (query === null) return null;

  if (isBiAccessDenied(probe.error)) {
    return <RestrictedWidget height={height} />;
  }

  const refusal = biRefusalSentence(probe.error);
  if (refusal !== null) {
    return <RefusedWidget sentence={refusal} height={height} />;
  }

  if (probe.error) {
    return (
      <ErrorPanel
        error={probe.error}
        onRetry={() => void probe.refetch()}
        title="Could not load these rows"
      />
    );
  }

  if (probe.isPending || page === undefined) {
    return (
      <div className="card space-y-2 p-5" aria-busy="true">
        <SkeletonLine width="70%" />
        <SkeletonLine width="55%" />
        <SkeletonLine width="62%" />
        <SkeletonLine width="48%" />
      </div>
    );
  }

  if (page.rows.length === 0) {
    return (
      <NeedsDataWidget dataset={dataset} coverage={coverage} height={height} />
    );
  }

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <TrustBadge
          status={page.trust?.status}
          failingChecks={page.trust?.failingChecks ?? []}
          size="compact"
        />
        <p className="text-caption text-slate">
          {page.usedAggregate
            ? "Read from the pre-aggregated tables."
            : "Read from the position book."}
        </p>
      </div>

      {truncated && (
        <p
          role="status"
          className="rounded-md border border-warn/30 bg-warn-light px-3 py-2 text-caption text-navy"
        >
          {truncationNotice(page.rows.length)}
        </p>
      )}

      {sortNotice && (
        <p role="status" className="text-caption text-slate">
          {sortNotice}
        </p>
      )}

      {pagingError && (
        <p
          role="alert"
          className="rounded-md border border-crit/30 bg-crit-light px-3 py-2 text-caption text-navy"
        >
          {pagingError}
        </p>
      )}

      <div style={{ height }}>
        <AgGridReact<GridRowData>
          // `cacheBlockSize` and `infiniteInitialRowCount` are read once, when
          // the grid is constructed. Keying on the page size rebuilds the grid
          // rather than leaving it configured for the previous answer.
          key={`bi-grid-${pageSize}`}
          theme={theme}
          columnDefs={columnDefs}
          rowModelType="infinite"
          datasource={datasource}
          cacheBlockSize={pageSize ?? undefined}
          maxBlocksInCache={PAGES_KEPT}
          infiniteInitialRowCount={pageSize ?? undefined}
          maxConcurrentDatasourceRequests={1}
          blockLoadDebounceMillis={150}
          // The ungoverned ways out of a BI view, closed. See the file header.
          suppressCsvExport
          suppressExcelExport
          suppressCellFocus={false}
          rowHeight={34}
          headerHeight={38}
          getRowClass={(params) =>
            (params.data as GridRowData | undefined)?.subtotal === true
              ? "aeq-bi-subtotal-row"
              : undefined
          }
          defaultColDef={{ resizable: true, suppressMovable: true }}
        />
      </div>
    </section>
  );
}

/**
 * The grid, painted from the SAME resolved design tokens the BI charts use.
 *
 * `echartsTheme.ts` resolves every colour by asking the document for the
 * computed value of each CSS custom property, because a canvas cannot read
 * `var()`. The grid could read `var()` — but then a chart and a grid on one
 * screen would be painted from two different resolutions of the same token and
 * could disagree about the brand. So both go through one resolver, and the theme
 * is rebuilt when the palette's digest changes, which is what a theme switch
 * produces.
 */
function useAgGridTheme() {
  const { tokens, name } = useChartTheme();
  return useMemo(
    () =>
      themeQuartz.withParams({
        accentColor: tokens.accent,
        backgroundColor: tokens.surface,
        borderColor: tokens.border,
        browserColorScheme: "inherit",
        chromeBackgroundColor: tokens.surface,
        foregroundColor: tokens.text,
        headerBackgroundColor: tokens.surface,
        headerTextColor: tokens.muted,
        oddRowBackgroundColor: tokens.surface,
        rowHoverColor: tokens.grid,
        subtleTextColor: tokens.muted,
        fontFamily:
          'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        fontSize: 12,
        headerFontSize: 11,
        headerFontWeight: 600,
        borderRadius: 6,
        wrapperBorder: { color: tokens.border, width: 1 },
        rowBorder: { color: tokens.grid, width: 1 },
      }),
    // The digest changes exactly when a resolved token value does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [name],
  );
}
