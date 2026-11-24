import type { ReactNode } from "react";
import { Sidebar } from "@/components/sidebar";
import { getApi, getMe } from "@/lib/server/api";
import { optional } from "@/lib/server/load";
import { portalConfig } from "@/lib/server/config";

/** Only a sandbox is labelled: it is the one environment outsiders rehearse filings in. */
function environmentBanner(): string | null {
  const config = portalConfig();
  if (config.mock) return "Concept design · synthetic data";
  return config.env === "sandbox" ? "Sandbox · not official filings" : null;
}

/** The app shell: the design's sidebar beside (or, on narrow screens, above) the page. */
export default async function PortalLayout({ children }: { children: ReactNode }) {
  const api = await getApi();
  const me = await getMe();
  const [submissions, queries] = await Promise.all([optional(api.listSubmissions({ status: "needs_review", limit: 1 })), optional(api.listQueries({ status: "open" }))]);
  return (
    <div className="box-border flex min-h-screen flex-wrap bg-canvas text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-4 focus:py-3 focus:text-[14px] focus:font-semibold">
        Skip to main content
      </a>
      <div className="flex max-w-full flex-[1_1_248px] bg-nav">
        <Sidebar me={me} badges={{ submissions: submissions?.counts.needs_review ?? null, queries: queries?.counts.open ?? null }} banner={environmentBanner()} />
      </div>
      <main id="main" className="box-border flex min-w-0 flex-[999_1_560px] flex-col gap-5 px-4 pb-12 pt-7 sm:px-8">
        {children}
      </main>
    </div>
  );
}
