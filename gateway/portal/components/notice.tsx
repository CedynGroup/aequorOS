"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

/** The success banner after an action redirected back; focused so it is read out. */
export function Notice({ message, dismissHref }: { message: string; dismissHref: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [message]);
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-intact-line bg-intact px-[18px] py-3 text-[14px] font-medium text-intact-title"
    >
      <span className="inline-flex items-center gap-2">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 12l4 4L19 6" />
        </svg>
        {message}
      </span>
      <Link href={dismissHref} scroll={false} className="inline-flex min-h-11 items-center text-[13px] text-intact-text">
        Dismiss
      </Link>
    </div>
  );
}
