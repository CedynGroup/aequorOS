"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  const digest = error instanceof Error && "digest" in error ? String((error as { digest?: string }).digest ?? "") : "";
  return (
    <main className="mx-auto my-12 box-border flex max-w-lg flex-col gap-3 rounded-[10px] border border-line bg-white px-6 py-6">
      <h1 className="m-0 text-[20px] font-semibold">This page could not be shown</h1>
      <p className="m-0 text-[14px] leading-[1.5] text-ink-2">The portal or the gateway service had a problem. Try again; if it keeps happening, tell the gateway’s support team{digest ? ` and quote reference ${digest}` : ""}.</p>
      <div className="flex gap-2">
        <button type="button" onClick={() => retry()} className="inline-flex min-h-11 items-center rounded-lg bg-action px-[18px] text-[14px] font-semibold text-white hover:bg-action-hover">
          Try again
        </button>
        <Link href="/" className="inline-flex min-h-11 items-center rounded-lg border border-field px-4 text-[14px] font-medium text-ink no-underline">
          Go to the overview
        </Link>
      </div>
    </main>
  );
}
