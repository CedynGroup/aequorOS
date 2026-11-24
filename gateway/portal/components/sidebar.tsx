import type { Me } from "@/lib/api/types";
import { initials } from "@/lib/format";
import { roleTitle } from "@/lib/roles";
import { signOut } from "@/lib/server/auth-actions";
import { NavLinks, type NavGroup } from "./nav-links";

export const ICONS = {
  overview: "M4 4h7v7H4z M13 4h7v4h-7z M13 10h7v10h-7z M4 13h7v7H4z",
  inbox: "M4 13l2.5-8h11L20 13 M4 13v6h16v-6 M4 13h5l1 2h4l1-2h5",
  queries: "M5 5h14v10H9l-4 4z",
  calendar: "M5 6h14v14H5z M5 10h14 M9 4v4 M15 4v4",
  institutions: "M4 10l8-5 8 5 M6 10v8 M10 10v8 M14 10v8 M18 10v8 M4 20h16",
  returns: "M7 4h7l4 4v12H7z M14 4v4h4 M9.5 13h5 M9.5 16h5",
  onboarding: "M15 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10z M11.5 12.5L4 20 M7 17l2 2",
  audit: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z M9 12l2 2 4-4",
  access: "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M3 19c0-3 2.7-5 6-5s6 2 6 5 M16 6a3 3 0 0 1 0 5 M17 14c2.3.4 4 2.2 4 5",
};

/** The regulator mark is a placeholder until BoG supplies its own. */
export function Mark({ size = 40 }: { size?: number }) {
  return (
    <div
      role="img"
      aria-label="AequorOS"
      className="flex flex-none items-center justify-center bg-action font-bold text-white"
      style={{ width: size, height: size, borderRadius: size >= 48 ? 10 : 8, fontSize: size >= 48 ? 22 : 18 }}
    >
      A
    </div>
  );
}

export function Sidebar({ me, badges, banner }: { me: Me; badges: { submissions: number | null; queries: number | null }; banner: string | null }) {
  const groups: NavGroup[] = [
    {
      label: "Supervision",
      items: [
        { key: "overview", label: "Overview", href: "/", icon: ICONS.overview },
        { key: "inbox", label: "Submissions", href: "/submissions", icon: ICONS.inbox, badge: badges.submissions, badgeLabel: "need review" },
        { key: "queries", label: "Queries", href: "/queries", icon: ICONS.queries, badge: badges.queries, badgeLabel: "open" },
        { key: "calendar", label: "Filing calendar", href: "/calendar", icon: ICONS.calendar },
        { key: "institutions", label: "Institutions", href: "/institutions", icon: ICONS.institutions },
      ],
    },
    {
      label: "Configuration",
      items: [
        { key: "returns", label: "Return definitions", href: "/returns", icon: ICONS.returns },
        { key: "onboarding", label: "Channel register", href: "/onboarding", icon: ICONS.onboarding },
      ],
    },
    {
      label: "Assurance",
      items: [
        { key: "audit", label: "Audit log", href: "/audit", icon: ICONS.audit },
        { key: "access", label: "Access & approvals", href: "/access", icon: ICONS.access },
      ],
    },
  ];
  return (
    <nav aria-label="Primary" className="on-navy box-border flex min-h-full w-full flex-col gap-7 bg-nav px-3.5 pb-6 pt-[22px] text-nav-text">
      <div className="flex items-center gap-3 px-1.5">
        <Mark />
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-[14px] font-semibold leading-[1.2] text-white">AequorOS Portal</span>
          <span className="text-[12px] text-nav-subtle">Banking Supervision</span>
        </div>
      </div>
      <NavLinks groups={groups} />
      <div className="mt-auto flex flex-col gap-3">
        {banner && <div className="rounded-md bg-concept px-2.5 py-2 text-[12px] leading-[1.4] text-concept-text">{banner}</div>}
        <div className="flex items-center gap-2.5 rounded-lg bg-nav-card p-2.5">
          <div aria-hidden="true" className="flex size-[34px] flex-none items-center justify-center rounded-full bg-nav-badge text-[13px] font-semibold text-white">
            {initials(me.name)}
          </div>
          <div className="flex min-w-0 flex-auto flex-col">
            <span className="text-[13px] font-semibold text-white">{me.name}</span>
            <span className="text-[12px] text-nav-subtle">{roleTitle(me.roles)}</span>
          </div>
        </div>
        <form action={signOut}>
          <button type="submit" className="flex min-h-11 w-full items-center justify-center rounded-lg border border-nav-badge bg-transparent text-[13px] font-medium text-nav-text hover:bg-nav-card hover:text-white">
            Sign out
          </button>
        </form>
      </div>
    </nav>
  );
}
