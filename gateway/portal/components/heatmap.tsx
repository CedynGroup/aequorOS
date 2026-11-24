import Link from "next/link";
import type { HeatState } from "@/lib/api/types";
import { cx } from "./ui";

const FILL: Record<HeatState, string> = {
  on_time: "bg-heat-on-time",
  late: "bg-heat-late",
  outstanding: "bg-heat-missing",
  not_due: "bg-heat-not-due",
};

/** Late cells carry a clock as well as their colour, so state is never colour alone. */
function Glyph({ state, small }: { state: HeatState; small?: boolean }) {
  if (state === "outstanding") return <>!</>;
  if (state === "late") {
    const size = small ? 11 : 14;
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#0E1726" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7.5V12l3 2" />
      </svg>
    );
  }
  return null;
}

export function HeatCell({ state, href, label, minWidth = 34 }: { state: HeatState; href: string | null; label: string; minWidth?: number }) {
  const className = cx(
    "flex h-[30px] w-full items-center justify-center rounded-[5px] text-[13px] font-bold text-white no-underline hover:text-white",
    FILL[state],
    href && "hover:opacity-85",
  );
  const style = { minWidth };
  if (href) {
    return (
      <Link href={href} aria-label={label} title={label} className={className} style={style}>
        <Glyph state={state} />
      </Link>
    );
  }
  return (
    <span role="img" aria-label={label} title={label} className={className} style={style}>
      <Glyph state={state} />
    </span>
  );
}

export function HeatLegend({ states, labels }: { states: HeatState[]; labels: Record<HeatState, { label: string }> }) {
  return (
    <ul aria-label="Key" className="m-0 flex list-none flex-wrap gap-3.5 p-0 text-[12px] text-ink-2">
      {states.map((state) => (
        <li key={state} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className={cx("inline-flex size-4 items-center justify-center rounded text-[11px] font-bold text-white", FILL[state])}>
            <Glyph state={state} small />
          </span>
          {labels[state].label}
        </li>
      ))}
    </ul>
  );
}
