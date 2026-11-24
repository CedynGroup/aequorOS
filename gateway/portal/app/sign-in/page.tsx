import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Mark } from "@/components/sidebar";
import { safeReturnTo } from "@/lib/auth/return-to";
import { param, type SearchParams } from "@/lib/search-params";
import { portalConfig } from "@/lib/server/config";
import { getSession } from "@/lib/server/session";

export const metadata: Metadata = { title: "Sign in" };

const POINTS = [
  {
    title: "Named accounts, role by role",
    text: "Every supervisor signs in with their own account. What they may do follows the roles on that account, and disabling it removes access at once.",
    icon: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z M9 12l2 2 4-4",
  },
  {
    title: "Filings are signed and receipted",
    text: "Each return carries the institution’s signature and receives a signed, timestamped receipt that cannot be altered.",
    icon: "M7 4h7l4 4v12H7z M14 4v4h4 M9.5 14l2 2 3.5-4",
  },
  {
    title: "Data stays under the Bank’s keys",
    text: "Stored filings are encrypted under a key the Bank holds. Operator access needs the Bank’s time-boxed approval.",
    icon: "M15 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10z M11.5 12.5L4 20 M7 17l2 2",
  },
];

const ERRORS: Record<string, string> = {
  session_expired: "Your session has ended. Sign in again to continue.",
  idp_unreachable: "The sign-in service could not be reached. Try again in a moment.",
  idp_refused: "The sign-in service did not sign you in.",
  sign_in_expired: "The sign-in took too long or was started in another window. Start again.",
  state_mismatch: "The sign-in could not be confirmed. Start again.",
  token_exchange_failed: "The sign-in service did not complete the sign-in. Try again.",
  nonce_mismatch: "The sign-in could not be confirmed. Start again.",
  issuer_mismatch: "The sign-in came from an identity provider this portal does not trust.",
  audience_mismatch: "The sign-in was issued for a different system.",
  malformed_id_token: "The sign-in service sent an identity this portal could not read.",
  no_subject: "The sign-in service sent an identity without a staff account.",
  expired: "The sign-in had already expired. Sign in again.",
};

export default async function SignInPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const next = safeReturnTo(param(params, "next"));
  if (await getSession()) redirect(next);
  const config = portalConfig();
  const errorCode = param(params, "error");
  const error = errorCode ? (ERRORS[errorCode] ?? "The sign-in did not complete. Try again.") : null;
  const signedOut = param(params, "signed_out") === "1";
  const banner = config.env === "sandbox" ? "Sandbox · not official filings" : null;

  return (
    <div className="box-border flex min-h-screen flex-wrap bg-canvas font-sans text-ink">
      <section aria-label="About this system" className="on-navy box-border flex flex-[1_1_480px] flex-col gap-10 bg-nav px-6 py-12 text-nav-text sm:px-14">
        <div className="flex items-center gap-3.5">
          <Mark size={48} />
          <div className="flex flex-col gap-0.5">
            <span className="text-[16px] font-semibold text-white">AequorOS Portal</span>
            <span className="text-[13px] text-nav-subtle">Banking Supervision</span>
          </div>
        </div>
        <div className="mt-auto flex max-w-[520px] flex-col gap-4">
          <p className="m-0 text-[36px] font-semibold leading-[1.15] tracking-[-0.015em] text-white">Every regulatory return, received, verified and on record.</p>
          <p className="m-0 text-[16px] leading-[1.55] text-nav-copy">Licensed institutions file here through one published API. Supervisors review, query and accept filings with a complete evidence trail.</p>
        </div>
        <ul className="m-0 mb-auto flex max-w-[520px] list-none flex-col gap-[18px] p-0">
          {POINTS.map((point) => (
            <li key={point.title} className="flex items-start gap-3.5">
              <span aria-hidden="true" className="flex size-8 flex-none items-center justify-center rounded-lg bg-nav-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#9DB8E8" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={point.icon} />
                </svg>
              </span>
              <span className="flex flex-col gap-1">
                <span className="text-[15px] font-semibold text-white">{point.title}</span>
                <span className="text-[14px] leading-[1.5] text-nav-copy">{point.text}</span>
              </span>
            </li>
          ))}
        </ul>
        {banner && <span className="text-[12px] text-nav-subtle">{banner}</span>}
      </section>

      <main className="box-border flex flex-[1_1_480px] items-center justify-center px-4 py-12 sm:px-8">
        <section aria-labelledby="signin-title" className="flex w-full max-w-[420px] flex-col gap-[22px] rounded-[14px] border border-line bg-white px-6 py-9 sm:px-8">
          <div className="flex flex-col gap-2">
            <h1 id="signin-title" className="m-0 text-[24px] font-semibold">Sign in</h1>
            <p className="m-0 text-[14px] leading-[1.5] text-muted">Use your AequorOS Portal account.</p>
          </div>

          {error && (
            <p role="alert" className="m-0 rounded-lg border border-bad-soft bg-bad-soft px-4 py-3 text-[14px] font-medium text-bad">{error}</p>
          )}
          {signedOut && !error && (
            <p role="status" className="m-0 rounded-lg border border-intact-line bg-intact px-4 py-3 text-[14px] font-medium text-intact-title">You have signed out of this portal.</p>
          )}

          <a href={`/auth/login${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} className="flex min-h-12 items-center justify-center gap-2.5 rounded-lg bg-action text-[15px] font-semibold text-white no-underline hover:bg-action-hover hover:text-white">
            Sign in
          </a>

          <div className="flex flex-col gap-2.5 rounded-lg bg-canvas px-4 py-3.5 text-[13px] leading-[1.5] text-ink-2">
            <span>
              Your password is checked by the AequorOS identity service and never reaches this portal. Your role here follows your account.
            </span>
          </div>
          <div className="h-px bg-hairline" />
          <div className="flex flex-col gap-1.5 text-[13px] leading-[1.5] text-muted">
            <span className="font-semibold text-ink">Filing for an institution?</span>
            <span>Institutions file through the API or the institution portal, with credentials issued when the channel operator enrols them. Their technical contact has the details.</span>
          </div>
          <span className="text-[12px] leading-[1.5] text-muted">Restricted system. Every sign-in and action is recorded in a tamper-evident audit log.</span>
        </section>
      </main>
    </div>
  );
}
