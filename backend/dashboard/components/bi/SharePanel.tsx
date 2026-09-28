"use client";

/**
 * Who a saved dashboard reaches — and, said plainly, what that does and does not
 * give them.
 *
 * SHARING A DASHBOARD DOES NOT SHARE ITS FIGURES, AND A READER WHO DOES NOT KNOW
 * THAT WILL SHARE A BOARD PACK BELIEVING THE RECIPIENT SEES IT. Every view on a
 * shared dashboard is authorized for whoever opens it, against their own access,
 * every time — so a colleague sees the views their access already covers and, in
 * the place of the others, that a view is restricted and nothing whatever about
 * it. That sentence is on this panel rather than in a tooltip, because it is the
 * single most consequential thing to misunderstand about this feature.
 *
 * THE LIST IS THE OWNER'S ALONE. The server refuses it to anybody else — being
 * able to open a document is not being told who else can — so this panel is only
 * rendered for the owner, and the server decides again.
 *
 * WHY THE PEOPLE ARE CHOSEN FROM A DIRECTORY AND NOT TYPED. The share route names
 * identities by id, not by address, so there is nothing to type. Reading this
 * organization's directory needs its own Account authority, which an analyst who
 * may build a dashboard does not hold — so when the directory is refused this
 * panel says so and points at the ways of sharing that need no directory at all
 * (a role, or the whole institution). It does not fall back to a blank box that
 * cannot work.
 *
 * AND NAMING PEOPLE IS ONLY OFFERED WHEN NAMING PEOPLE IS HOW THIS DASHBOARD IS
 * SHARED. The server refuses the list outright for any other reachability — "this
 * dashboard is not shared with named people, so there is no list to set" — and it
 * DELETES the names whenever the owner saves a different setting, because a share
 * row no visibility reads is a permission nobody can see. Offering the control
 * anyway would be a button that answers 403, so the panel states the setting it is
 * in and where to change it instead.
 */

import { useMemo, useState } from "react";
import { Check, Trash2, Users } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { SkeletonLine } from "@/components/ui/Skeleton";
import { addressableRoleLabel } from "./builder";
import type { BiDashboardVisibility } from "./types";
import type { BiDashboardShareRead } from "@aequoros/risk-service-api";

/** One tenant identity that may be named. */
export type ShareCandidate = Readonly<{
  id: string;
  displayName: string | null;
  email: string;
  isActive: boolean;
}>;

export const SHARING_GRANTS_NOTHING =
  "Sharing a dashboard does not share its figures. Everyone you name is " +
  "authorized view by view when they open it, against their own access — so a " +
  "colleague sees the views their access already covers, and in the place of the " +
  "others that a view is restricted and nothing about what it holds.";

/** What the current reachability setting means, in the reader's words. */
export function reachabilitySentence(
  visibility: BiDashboardVisibility,
  role: string | null,
): string {
  if (visibility === "private") {
    return "Only you can open this dashboard. Nobody else is told it exists.";
  }
  if (visibility === "users") {
    return "Only the people named below can open this dashboard.";
  }
  if (visibility === "role") {
    return role
      ? `Anyone holding ${addressableRoleLabel(role)} over this institution can open this dashboard.`
      : "A role can open this dashboard.";
  }
  return (
    "Anyone at your organization whose access covers this institution can open " +
    "this dashboard. It never reaches another organization."
  );
}

export type SharePanelProps = Readonly<{
  visibility: BiDashboardVisibility;
  visibilityRole: string | null;
  /** The current list, from `GET …/bi/dashboards/{id}/shares`. */
  shares: readonly BiDashboardShareRead[] | undefined;
  sharesLoading: boolean;
  sharesError: unknown;
  /** This organization's identities, when the reader may read the directory. */
  candidates: readonly ShareCandidate[] | undefined;
  directoryRefused: boolean;
  directoryLoading: boolean;
  /** Where the reader goes to change WHO can open it at all. */
  editHref: string | null;
  saving: boolean;
  saveError: unknown;
  onSave: (userIds: readonly string[]) => void;
}>;

export default function SharePanel({
  visibility,
  visibilityRole,
  shares,
  sharesLoading,
  sharesError,
  candidates,
  directoryRefused,
  directoryLoading,
  editHref,
  saving,
  saveError,
  onSave,
}: SharePanelProps) {
  const named = useMemo(
    () => (shares ?? []).map((share) => share.userId),
    [shares],
  );
  const [adding, setAdding] = useState("");

  // Naming people is only offered when naming people is the reachability rule:
  // the server refuses the list for any other one, and clears it on any other save.
  const byName = visibility === "users";
  const alreadyNamed = new Set(named);
  const addable = (candidates ?? []).filter(
    (person) => !alreadyNamed.has(person.id) && person.isActive,
  );

  return (
    <SectionCard
      title="Sharing"
      subtitle={reachabilitySentence(visibility, visibilityRole)}
      actions={
        editHref && (
          <a
            href={editHref}
            className="rounded-md border border-border px-2.5 py-1 text-caption font-medium text-action hover:bg-surface"
          >
            Change who can open it
          </a>
        )
      }
    >
      <div className="space-y-4">
        <p className="text-caption leading-relaxed text-slate">
          {SHARING_GRANTS_NOTHING}
        </p>

        {!byName && (
          <p className="text-caption leading-relaxed text-slate">
            This dashboard is not shared by name, so there is no list of people
            on it. Change who can open it to name people one by one — and note
            that switching away from naming people again clears the list,
            because a name nothing reads is an access nobody can see.
          </p>
        )}

        {byName && sharesLoading && (
          <div aria-busy="true" className="space-y-2">
            <SkeletonLine width="60%" />
            <SkeletonLine width="45%" />
          </div>
        )}

        {byName && Boolean(sharesError) && (
          <ErrorPanel
            error={sharesError}
            title="Could not read who this dashboard is shared with"
          />
        )}

        {byName && shares && (
          <div className="space-y-2">
            <p className="inline-flex items-center gap-1.5 text-caption font-medium text-navy">
              <Users size={13} aria-hidden />
              {shares.length === 1
                ? "1 person is named"
                : `${shares.length} people are named`}
            </p>
            {shares.length === 0 ? (
              <p className="text-body text-slate">Nobody is named yet.</p>
            ) : (
              <ul className="divide-y divide-border-light rounded-md border border-border">
                {shares.map((share) => (
                  <li
                    key={share.userId}
                    className="flex items-center justify-between gap-3 px-3 py-2"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-body text-navy">
                        {share.displayName ?? share.email}
                      </span>
                      <span className="block truncate text-caption text-slate">
                        {share.email}
                      </span>
                    </span>
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() =>
                        onSave(named.filter((id) => id !== share.userId))
                      }
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-critical hover:bg-critical-light disabled:opacity-50"
                    >
                      <Trash2 size={12} aria-hidden />
                      Take off the list
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {byName && directoryLoading && !directoryRefused && (
          <div aria-busy="true">
            <SkeletonLine width="40%" />
          </div>
        )}

        {byName && directoryRefused && (
          <p className="text-caption leading-relaxed text-slate">
            Naming a colleague needs the account directory, and your access does
            not cover it. You can still share this dashboard with everyone
            holding a role, or with everyone whose access covers this
            institution — change who can open it above. An organization owner
            can grant directory access.
          </p>
        )}

        {byName && shares && !directoryRefused && candidates && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-60 flex-col gap-1.5">
              <span className="text-caption font-medium text-navy">
                Name someone else
              </span>
              <select
                value={adding}
                onChange={(event) => setAdding(event.target.value)}
                aria-label="Name someone else"
                className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-none"
              >
                <option value="">Choose a colleague</option>
                {addable.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.displayName
                      ? `${person.displayName} (${person.email})`
                      : person.email}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={adding === "" || saving}
              onClick={() => {
                onSave([...named, adding]);
                setAdding("");
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50"
            >
              <Check size={13} aria-hidden />
              Add to the list
            </button>
            {addable.length === 0 && (
              <p className="text-caption text-slate">
                Everyone with an active account is already named.
              </p>
            )}
          </div>
        )}

        {Boolean(saveError) && (
          <ErrorPanel error={saveError} title="The list was not changed" />
        )}
      </div>
    </SectionCard>
  );
}
