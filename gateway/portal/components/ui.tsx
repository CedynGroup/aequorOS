import Link from "next/link";
import type { ReactNode } from "react";
import type { Tone } from "@/lib/labels";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

const TONES: Record<Tone, string> = {
  green: "bg-ok-soft text-ok",
  blue: "bg-action-soft text-action",
  grey: "bg-neutral-soft text-ink-2",
  red: "bg-bad-soft text-bad",
  purple: "bg-query-soft text-query",
  amber: "bg-warn-soft text-warn",
  muted: "bg-canvas text-muted",
  struck: "bg-canvas text-muted line-through",
};

/** A status chip. The label is always text, so status is never colour alone. */
export function Chip({ tone, children, size = "md", className }: { tone: Tone; children: ReactNode; size?: "sm" | "md"; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center whitespace-nowrap rounded-full font-semibold",
        size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-[3px] text-[12px]",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export const button = {
  primary:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border-0 bg-action px-[18px] text-[14px] font-semibold text-white no-underline hover:bg-action-hover hover:text-white disabled:cursor-not-allowed disabled:bg-line disabled:text-muted",
  secondary:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-field bg-white px-4 text-[14px] font-medium text-ink no-underline hover:bg-panel hover:text-ink disabled:cursor-not-allowed disabled:border-line disabled:bg-canvas disabled:text-disabled",
  quiet:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border-0 bg-neutral-soft px-4 text-[14px] font-medium text-ink no-underline hover:bg-line hover:text-ink",
  small:
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-field bg-white px-3.5 text-[13px] font-medium text-ink no-underline hover:bg-panel hover:text-ink disabled:cursor-not-allowed disabled:border-line disabled:bg-canvas disabled:text-disabled",
};

export const field = {
  label: "text-[12px] font-semibold text-ink-2",
  input:
    "box-border h-11 w-full rounded-lg border border-field bg-white px-3 text-[14px] text-ink placeholder:text-disabled",
  select: "box-border h-11 w-full rounded-lg border border-field bg-white px-2.5 text-[14px] text-ink",
  textarea:
    "box-border w-full resize-y rounded-lg border border-field bg-white px-3.5 py-3 text-[14px] leading-[1.5] text-ink placeholder:text-disabled",
};

export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLElement>) {
  return (
    <section className={cx("flex min-w-0 flex-col rounded-[10px] border border-line bg-white", className)} {...rest}>
      {children}
    </section>
  );
}

export function CardTitle({ id, children, as = "h2", className }: { id?: string; children: ReactNode; as?: "h2" | "h3"; className?: string }) {
  const Tag = as;
  return (
    <Tag id={id} className={cx("m-0 text-[17px] font-semibold", className)}>
      {children}
    </Tag>
  );
}

export type Crumb = { label: string; href?: string; mono?: boolean };

export function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="text-[13px] text-muted">
      {items.map((item, i) => (
        <span key={`${item.label}-${i}`}>
          {i > 0 && " / "}
          {item.href ? (
            <Link href={item.href} className="text-muted hover:text-ink">
              {item.label}
            </Link>
          ) : (
            <span className={item.mono ? "font-mono" : undefined} aria-current={i === items.length - 1 ? "page" : undefined}>
              {item.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}

/** The page header used by list screens: crumb, title, subtitle, actions. */
export function PageHeader({
  crumbs,
  title,
  subtitle,
  actions,
}: {
  crumbs: Crumb[];
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1.5">
        <Breadcrumb items={crumbs} />
        <h1 className="m-0 text-[28px] font-semibold tracking-[-0.01em]">{title}</h1>
        {subtitle && <p className="m-0 text-[14px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
    </header>
  );
}

/** A KPI tile (overview totals, institution stats). */
export function Stat({ label, value, note, tone = "ink" }: { label: string; value: ReactNode; note?: ReactNode; tone?: "ink" | "ok" | "bad" | "warn" | "query" }) {
  const toneClass = { ink: "text-ink", ok: "text-ok", bad: "text-bad", warn: "text-warn", query: "text-query" }[tone];
  return (
    <div className="flex flex-col gap-1.5 rounded-[10px] border border-line bg-white px-[18px] py-4">
      <span className="text-[13px] font-medium text-muted">{label}</span>
      <span className={cx("tabular text-[28px] font-semibold tracking-[-0.01em]", toneClass)}>{value}</span>
      {note && <span className="text-[12px] text-muted">{note}</span>}
    </div>
  );
}

/** A labelled definition row, label left and value right. */
export function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="m-0 text-right font-medium">{children}</dd>
    </div>
  );
}

/** A progress bar with its value as the accessible name. */
export function Progress({ value, total, label }: { value: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div role="img" aria-label={label} className="flex h-2 overflow-hidden rounded-full bg-hairline">
      <span className="bg-heat-on-time" style={{ width: `${pct}%` }} />
    </div>
  );
}

/**
 * A control the design shows but the gateway does not offer yet. It stays in
 * the tab order and says why it does nothing, instead of silently failing.
 */
export function Unavailable({ children, reason, size = "md", className }: { children: ReactNode; reason: string; size?: "md" | "sm"; className?: string }) {
  return (
    <button
      type="button"
      aria-disabled="true"
      title={reason}
      className={cx(
        "inline-flex min-h-11 cursor-not-allowed items-center justify-center gap-2 rounded-lg border border-dashed border-field bg-canvas font-medium text-disabled",
        size === "sm" ? "px-3.5 text-[13px]" : "px-4 text-[14px]",
        className,
      )}
    >
      {children}
      <span className="sr-only">: {reason}</span>
    </button>
  );
}

/** A page whose data the API refused or could not supply, shown inside the shell. */
export function PageProblem({ crumbs, title, kind, message }: { crumbs: Crumb[]; title: string; kind: "forbidden" | "unavailable"; message: string }) {
  return (
    <>
      <PageHeader crumbs={crumbs} title={title} />
      <div role="alert" className={cx("rounded-[10px] border px-[18px] py-4 text-[14px] leading-[1.5]", kind === "forbidden" ? "border-line bg-white text-ink-2" : "border-note-line bg-note text-note-text")}>
        <p className="m-0 font-semibold">{kind === "forbidden" ? "Not available to your role" : "This page could not be loaded"}</p>
        <p className="m-0 mt-1">{message}</p>
      </div>
    </>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="m-0 px-[18px] py-6 text-[14px] text-muted">{children}</p>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono", className)}>{children}</span>;
}

export const th = "border-b border-line px-3 py-2.5 text-left text-[12px] font-semibold text-muted";
export const td = "border-b border-hairline px-3 py-3 align-top";
