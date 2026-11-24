"use client";

import { useRef, useState, type ReactNode } from "react";

/**
 * The inbox's row selection. The checkboxes are plain form fields, so
 * "Export selected" works without JavaScript; with it, the count and the
 * "select all on this page" box stay in step.
 */
export function SelectableForm({ action, children, actions }: { action: string; children: ReactNode; actions: ReactNode }) {
  const ref = useRef<HTMLFormElement>(null);
  const [count, setCount] = useState(0);

  function boxes(): HTMLInputElement[] {
    return Array.from(ref.current?.querySelectorAll<HTMLInputElement>('input[name="receipt"]') ?? []);
  }

  function onChange(event: React.ChangeEvent<HTMLFormElement>) {
    const target = event.target as unknown as HTMLInputElement;
    if (target.dataset.selectAll !== undefined) {
      for (const box of boxes()) box.checked = target.checked;
    }
    const all = boxes();
    const checked = all.filter((b) => b.checked).length;
    setCount(checked);
    const master = ref.current?.querySelector<HTMLInputElement>("input[data-select-all]");
    if (master) {
      master.checked = checked > 0 && checked === all.length;
      master.indeterminate = checked > 0 && checked < all.length;
    }
  }

  return (
    <form ref={ref} action={action} method="get" onChange={onChange} className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-t-[10px] border-b border-hairline bg-panel px-[18px] py-3">
        <span className="text-[13px] text-ink-2" aria-live="polite">
          {count > 0 ? (
            <>
              <strong className="font-semibold">{count} selected</strong> · assign to a supervisor or export
            </>
          ) : (
            "Select filings to assign or export them"
          )}
        </span>
        <div className="flex flex-wrap gap-2">{actions}</div>
      </div>
      {children}
    </form>
  );
}
