import type { ReactNode } from "react";
import { button, cx } from "./ui";

/** A button that opens a small decision panel beneath it (native disclosure; no script needed). */
export function ActionDisclosure({ label, variant = "secondary", group, children }: { label: string; variant?: "primary" | "secondary"; group?: string; children: ReactNode }) {
  return (
    <details name={group} className="relative">
      <summary className={cx(button[variant], "cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden")}>{label}</summary>
      <div className="absolute left-0 z-30 mt-2 w-[min(380px,calc(100vw-32px))] rounded-[10px] border border-line bg-white p-4 text-left shadow-[0_12px_32px_rgba(14,23,38,0.16)] sm:left-auto sm:right-0">
        {children}
      </div>
    </details>
  );
}
