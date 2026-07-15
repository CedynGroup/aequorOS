import type { ReactNode } from "react";

import { cn } from "../../../lib/utils";

export function DenseTable({
  ariaLabel,
  children,
}: {
  ariaLabel: string;
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table aria-label={ariaLabel} className="w-full border-collapse text-xs">
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={cn(
        "whitespace-nowrap border-b border-[rgb(var(--border))] px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  mono = false,
  tone = "default",
  emphasis = false,
  colSpan,
  title,
}: {
  children?: ReactNode;
  align?: "left" | "right";
  mono?: boolean;
  tone?: "default" | "muted" | "danger";
  emphasis?: boolean;
  colSpan?: number;
  title?: string;
}) {
  return (
    <td
      colSpan={colSpan}
      title={title}
      className={cn(
        "border-b border-[rgb(var(--border))] px-2 py-1",
        align === "right" && "text-right",
        mono && "font-mono tabular-nums",
        tone === "muted" && "text-[rgb(var(--muted-foreground))]",
        tone === "danger" && "text-[rgb(var(--danger))]",
        emphasis && "font-semibold",
      )}
    >
      {children}
    </td>
  );
}

export function NumCell({
  value,
  title,
  tone = "default",
  emphasis = false,
}: {
  value: string;
  title?: string;
  tone?: "default" | "muted" | "danger";
  emphasis?: boolean;
}) {
  return (
    <Td align="right" mono tone={tone} emphasis={emphasis} title={title}>
      {value}
    </Td>
  );
}
