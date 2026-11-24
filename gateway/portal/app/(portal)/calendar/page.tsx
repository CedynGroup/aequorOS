import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardTitle, PageHeader, PageProblem, cx } from "@/components/ui";
import type { CalendarEvent } from "@/lib/api/types";
import { addMonths, isMonth, monthGrid, monthOf } from "@/lib/calendar";
import { formatMonthYear, formatWeekdayDate, isoDate, plural } from "@/lib/format";
import { CALENDAR_TONE, cadenceLabel } from "@/lib/labels";
import { oneOf, param, type SearchParams } from "@/lib/search-params";
import { getApi } from "@/lib/server/api";
import { load } from "@/lib/server/load";

export const metadata: Metadata = { title: "Filing calendar" };

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TONE_CLASS: Record<CalendarEvent["tone"], string> = {
  past: "bg-cal-past text-action-hover",
  warning: "bg-warn-soft text-cal-warn-text",
  future: "bg-cal-future text-ink-2",
};

function eventDetail(e: CalendarEvent): string {
  return `${e.detail} · ${e.received === null ? "due" : `${e.received} / ${e.due}`}`;
}

function Event({ event }: { event: CalendarEvent }) {
  return (
    <Link href="/submissions" className={cx("flex flex-col gap-px rounded-md px-2 py-1.5 text-[12px] leading-[1.35] no-underline hover:opacity-90", TONE_CLASS[event.tone])}>
      <span className="sr-only">{CALENDAR_TONE[event.tone].srLabel}: </span>
      <span className="flex items-center gap-1 font-semibold">
        {event.tone === "warning" && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 4l9 16H3z M12 10v4 M12 17h.01" />
          </svg>
        )}
        {event.title}
      </span>
      <span>{eventDetail(event)}</span>
    </Link>
  );
}

export default async function CalendarPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const today = isoDate(new Date());
  const requested = param(params, "month");
  const month = isMonth(requested) ? requested : monthOf(new Date());
  const view = oneOf(param(params, "view"), ["month", "list"] as const, "month");
  const api = await getApi();
  const result = await load(api.calendar({ month }));
  const crumbs = [{ label: "Supervision" }, { label: "Filing calendar" }];
  if (!result.ok) return <PageProblem crumbs={crumbs} title="Filing calendar" kind={result.kind} message={result.message} />;
  const data = result.data;
  const byDate = new Map(data.days.map((d) => [d.date, d.events]));
  const label = formatMonthYear(`${month}-01`);
  const viewSuffix = view === "list" ? "&view=list" : "";

  return (
    <>
      <PageHeader
        crumbs={crumbs}
        title="Filing calendar"
        subtitle="Due dates come from the published return definitions; institutions see the same calendar"
        actions={
          <>
            <Link href={`/calendar?month=${addMonths(month, -1)}${viewSuffix}`} aria-label="Previous month" className="inline-flex size-11 items-center justify-center rounded-lg border border-field bg-white text-[18px] text-ink no-underline hover:bg-panel">
              ‹
            </Link>
            <span className="min-w-[140px] text-center text-[16px] font-semibold" aria-live="polite">{label}</span>
            <Link href={`/calendar?month=${addMonths(month, 1)}${viewSuffix}`} aria-label="Next month" className="inline-flex size-11 items-center justify-center rounded-lg border border-field bg-white text-[18px] text-ink no-underline hover:bg-panel">
              ›
            </Link>
            <nav aria-label="View" className="ml-1.5 inline-flex overflow-hidden rounded-lg border border-field">
              {(["month", "list"] as const).map((v, i) => (
                <Link
                  key={v}
                  href={`/calendar?month=${month}${v === "list" ? "&view=list" : ""}`}
                  aria-current={view === v ? "true" : undefined}
                  className={cx("inline-flex min-h-11 items-center px-3.5 text-[13px] no-underline", i > 0 && "border-l border-field", view === v ? "bg-action-soft font-semibold text-action" : "bg-white text-ink-2 hover:bg-panel")}
                >
                  {v === "month" ? "Month" : "List"}
                </Link>
              ))}
            </nav>
          </>
        }
      />

      <div className="flex flex-wrap items-start gap-5">
        {view === "month" ? (
          <section aria-label={label} className="min-w-0 flex-[3_1_640px] overflow-x-auto rounded-[10px] border border-line bg-white p-3.5">
            <div className="grid min-w-[700px] grid-cols-7 gap-1.5" role="grid" aria-readonly="true">
              <div role="row" className="contents">
                {WEEKDAYS.map((day) => (
                  <span key={day} role="columnheader" className="px-2 py-1 text-[12px] font-semibold text-muted">{day}</span>
                ))}
              </div>
              {Array.from({ length: 6 }, (_, week) => (
                <div key={week} role="row" className="contents">
                  {monthGrid(month)
                    .slice(week * 7, week * 7 + 7)
                    .map((cell) => {
                      const isToday = cell.date === today;
                      const events = byDate.get(cell.date) ?? [];
                      return (
                        <div
                          key={cell.date}
                          role="gridcell"
                          aria-current={isToday ? "date" : undefined}
                          className={cx(
                            "box-border flex min-h-[118px] flex-col gap-1.5 rounded-lg p-2",
                            isToday ? "border-2 border-action bg-white" : cell.inMonth ? "border border-hairline bg-white" : "border border-hairline bg-panel",
                          )}
                        >
                          <span className={cx("tabular text-[13px]", isToday ? "font-bold text-action" : cell.inMonth ? "font-medium text-ink" : "text-disabled")}>
                            {cell.label}
                            {isToday && <span className="sr-only"> (today)</span>}
                          </span>
                          {events.map((e, i) => (
                            <Event key={i} event={e} />
                          ))}
                        </div>
                      );
                    })}
                </div>
              ))}
            </div>
          </section>
        ) : (
          <Card aria-label={label} className="flex-[3_1_640px] px-[22px] py-5">
            {data.days.length === 0 ? (
              <p className="m-0 text-[14px] text-muted">Nothing falls due in {label}.</p>
            ) : (
              <ol className="m-0 list-none p-0">
                {data.days.map((day) => (
                  <li key={day.date} className="flex flex-wrap gap-4 border-t border-hairline py-3 first:border-t-0">
                    <span className={cx("w-28 flex-none text-[14px] font-semibold", day.date === today && "text-action")}>
                      {formatWeekdayDate(day.date)}
                      {day.date === today && " · today"}
                    </span>
                    <div className="flex min-w-0 flex-auto flex-wrap gap-2">
                      {day.events.map((e, i) => (
                        <div key={i} className="min-w-[220px]">
                          <Event event={e} />
                        </div>
                      ))}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        )}

        <div className="flex min-w-0 flex-[1_1_280px] flex-col gap-5">
          <Card aria-labelledby="week-title" className="gap-2.5 px-[22px] py-5">
            <CardTitle id="week-title">Due in the next 10 days</CardTitle>
            {data.upcoming.length === 0 ? (
              <p className="m-0 text-[14px] text-muted">Nothing falls due in the next 10 days.</p>
            ) : (
              <ul className="m-0 list-none p-0">
                {data.upcoming.map((item) => (
                  <li key={item.label} className="flex flex-col gap-[3px] border-t border-hairline py-2.5">
                    <span className="flex justify-between gap-2.5">
                      <span className="text-[14px] font-semibold">{item.label}</span>
                      <span className="text-[13px] text-muted">{item.count_label}</span>
                    </span>
                    <span className="text-[13px] leading-[1.4] text-ink-2">{item.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card aria-labelledby="cadence-title" className="gap-1 px-[22px] pb-[18px] pt-4">
            <CardTitle id="cadence-title" as="h2" className="text-[15px]">Cadence</CardTitle>
            <ul className="m-0 list-none p-0">
              {data.cadences.map((c) => (
                <li key={c.cadence} className="flex min-h-10 items-center gap-2.5 text-[14px] text-ink-2">
                  <span className="flex-auto">{cadenceLabel(c.cadence)}</span>
                  <span className="text-[12px] text-muted">{plural(c.returns, "return")}</span>
                </li>
              ))}
            </ul>
          </Card>

          <section className="rounded-[10px] border border-line bg-panel px-[18px] py-4 text-[13px] leading-[1.55] text-ink-2">
            Daily returns (DBK-DAILY) fall due every business day and are summarised on the overview. Extensions granted to an institution move only that institution’s due date and are recorded with the approver.
          </section>
        </div>
      </div>
    </>
  );
}
