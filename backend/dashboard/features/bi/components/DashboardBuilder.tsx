"use client";

/**
 * Arranging a saved dashboard.
 *
 * WHAT IS BEING EDITED IS A SET OF QUESTIONS, NOT A SET OF ANSWERS. Nothing on
 * this surface reads a figure: a tile states the heading its author gave it and
 * the catalogue figures it will ask for, and the answers arrive when somebody
 * opens the dashboard — authorized for THAT reader, widget by widget. So a canvas
 * can be arranged, shared and reopened without any figure passing through the
 * builder at all, and a bug here cannot disclose one.
 *
 * SAVING APPENDS A VERSION. The history is append-only in the database, so an
 * edit adds the new layout rather than overwriting the old one: a dashboard's
 * history can be extended and never rewritten, and the change note is the one
 * line that says why.
 *
 * ONLY THE OWNER REACHES THIS. The server decides that — reachability first (a
 * dashboard this identity may not open does not exist for them), then ownership —
 * and the surfaces that link here only do so for a document the caller owns. This
 * component is therefore not a second authority; it is the form the owner uses.
 *
 * TWO WAYS TO PLACE A TILE, ON PURPOSE. react-grid-layout carries the drag and
 * the corner handle; the width and height controls on each tile do the same job
 * from a keyboard. A canvas that can only be arranged with a mouse is a canvas
 * some of this bank's people cannot arrange.
 */

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { GripVertical, Pencil, Plus, Trash2 } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import BuilderGrid from "./BuilderGrid";
import WidgetComposer from "./WidgetComposer";
import { WIDGET_ROW_HEIGHT } from "./WidgetRenderer";
import {
  ADDRESSABLE_ROLES,
  BUILDER_COLUMNS,
  BUILDER_DRAG_HANDLE_CLASS,
  BUILDER_WIDGET_KINDS,
  CHANGE_NOTE_MAX,
  addressableRoleLabel,
  DASHBOARD_DESCRIPTION_MAX,
  DASHBOARD_TITLE_MAX,
  addWidget,
  applyLayout,
  draftProblems,
  layoutsEqual,
  removeWidget,
  replaceWidget,
  sizeWidget,
  type BuilderDraft,
} from "./builder";
import type { ExploreCatalogue } from "./exploreQuery";
import type { BiDashboardVisibility } from "./types";
import type { BiPackWidget } from "@aequoros/risk-service-api";

/**
 * Who a saved dashboard opens for. Reachability, never authority: each of these
 * people is authorized figure by figure when they open it.
 */
const VISIBILITIES: readonly {
  value: BiDashboardVisibility;
  label: string;
  help: string;
}[] = [
  {
    value: "private",
    label: "Only me",
    help: "Nobody else can open it, and nobody else is told it exists.",
  },
  {
    value: "users",
    label: "People I name",
    help: "You choose them one by one after saving, and can take a name off the list at any time.",
  },
  {
    value: "role",
    label: "Everyone holding a role",
    help: "Whoever holds that role over this institution, as their access stands at the moment they open it.",
  },
  {
    value: "org",
    label: "Everyone with access to this institution",
    help: "Anyone at this organization whose access covers this institution. It never leaves your organization.",
  },
];

function kindLabel(kind: string): string {
  return (
    BUILDER_WIDGET_KINDS.find((entry) => entry.kind === kind)?.label ?? "Table"
  );
}

export type DashboardBuilderProps = Readonly<{
  catalogue: ExploreCatalogue;
  initialDraft: BuilderDraft;
  /** Whether this is the first save of a new dashboard, or a further version. */
  mode: "create" | "edit";
  saving: boolean;
  error: unknown;
  onSave: (draft: BuilderDraft, changeNote: string) => void;
  cancelHref: string;
}>;

export default function DashboardBuilder({
  catalogue,
  initialDraft,
  mode,
  saving,
  error,
  onSave,
  cancelHref,
}: DashboardBuilderProps) {
  const [draft, setDraft] = useState<BuilderDraft>(initialDraft);
  const [composing, setComposing] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [changeNote, setChangeNote] = useState("");
  const [confirmingRemoval, setConfirmingRemoval] = useState<string | null>(
    null,
  );

  const problems = draftProblems(draft);
  const editing =
    draft.widgets.find((widget) => widget.id === editingId) ?? null;

  const measureLabel = useCallback(
    (id: string) =>
      catalogue.measures.find((measure) => measure.id === id)?.label ?? id,
    [catalogue],
  );
  const dimensionLabel = useCallback(
    (id: string) =>
      catalogue.dimensions.find((dimension) => dimension.id === id)?.label ??
      id,
    [catalogue],
  );

  // The grid reports a layout on mount as well as after a drag, so the geometry
  // is compared before state moves — otherwise the drag never settles.
  const onLayoutChange = useCallback(
    (
      layout: readonly {
        i: string;
        x: number;
        y: number;
        w: number;
        h: number;
      }[],
    ) => {
      setDraft((current) =>
        layoutsEqual(current.layout, layout)
          ? current
          : applyLayout(current, layout),
      );
    },
    [],
  );

  const items = useMemo(
    () =>
      draft.widgets.map((widget) => {
        const place = draft.layout.find((item) => item.i === widget.id);
        const figures = widget.query?.measures ?? [];
        const breakdown = widget.query?.dimensions ?? [];
        return {
          id: widget.id,
          node: (
            <div className="card flex h-full flex-col gap-2 overflow-hidden p-3">
              <div className="flex items-start gap-2">
                <span
                  className={`${BUILDER_DRAG_HANDLE_CLASS} mt-0.5 cursor-grab text-slate`}
                  aria-hidden
                >
                  <GripVertical size={14} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-body font-medium text-navy">
                    {widget.title}
                  </p>
                  {widget.caption ? (
                    <p className="truncate text-caption text-slate">
                      {widget.caption}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(widget.id);
                      setComposing(true);
                    }}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-micro text-slate hover:bg-surface"
                  >
                    <Pencil size={11} aria-hidden />
                    Change
                  </button>
                  {confirmingRemoval === widget.id ? (
                    <button
                      type="button"
                      onClick={() => {
                        setDraft((current) => removeWidget(current, widget.id));
                        setConfirmingRemoval(null);
                      }}
                      className="rounded-md border border-critical px-1.5 py-0.5 text-micro text-critical hover:bg-critical-light"
                    >
                      Take it off
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmingRemoval(widget.id)}
                      className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-micro text-critical hover:bg-critical-light"
                      aria-label={`Remove ${widget.title}`}
                    >
                      <Trash2 size={11} aria-hidden />
                      Remove
                    </button>
                  )}
                </div>
              </div>

              <div className="min-w-0 flex-1 overflow-hidden text-caption leading-relaxed text-slate">
                <p className="truncate">
                  {kindLabel(widget.kind)} of{" "}
                  {figures.map((id) => measureLabel(id)).join(", ")}
                </p>
                {breakdown.length > 0 && (
                  <p className="truncate">
                    Broken down by{" "}
                    {breakdown.map((id) => dimensionLabel(id)).join(", ")}
                  </p>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 text-micro text-slate">
                <label className="inline-flex items-center gap-1">
                  Columns
                  <input
                    type="number"
                    min={1}
                    max={BUILDER_COLUMNS}
                    value={place ? place.w : 1}
                    onChange={(event) =>
                      setDraft((current) =>
                        sizeWidget(current, widget.id, {
                          w: Number(event.target.value),
                        }),
                      )
                    }
                    aria-label={`Width of ${widget.title} in columns`}
                    className="w-14 rounded-sm border border-border bg-white px-1 py-0.5 text-micro text-navy"
                  />
                </label>
                <label className="inline-flex items-center gap-1">
                  Rows
                  <input
                    type="number"
                    min={1}
                    max={24}
                    value={place ? place.h : 1}
                    onChange={(event) =>
                      setDraft((current) =>
                        sizeWidget(current, widget.id, {
                          h: Number(event.target.value),
                        }),
                      )
                    }
                    aria-label={`Height of ${widget.title} in rows`}
                    className="w-14 rounded-sm border border-border bg-white px-1 py-0.5 text-micro text-navy"
                  />
                </label>
              </div>
            </div>
          ),
        };
      }),
    [
      confirmingRemoval,
      dimensionLabel,
      draft.layout,
      draft.widgets,
      measureLabel,
    ],
  );

  return (
    <div className="space-y-6">
      <SectionCard
        title="What this dashboard is"
        subtitle="The name and the note are what your colleagues see on the list."
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-caption font-medium text-navy">Name</span>
              <input
                type="text"
                value={draft.title}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    title: event.target.value,
                  }))
                }
                maxLength={DASHBOARD_TITLE_MAX}
                placeholder="Weekly funding review"
                className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-caption font-medium text-navy">
                What it is for
              </span>
              <input
                type="text"
                value={draft.description}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
                maxLength={DASHBOARD_DESCRIPTION_MAX}
                placeholder="Optional"
                className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
              />
            </label>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-caption font-medium text-navy">
              Who can open it
            </legend>
            <p className="text-caption leading-relaxed text-slate">
              Opening a dashboard is not the same as seeing its figures.
              Everyone here is authorized view by view when they open it, so a
              colleague sees only what their own access already covers — and
              where it does not, they see that a view is restricted and nothing
              about it.
            </p>
            <div className="space-y-1.5">
              {VISIBILITIES.map((option) => (
                <label
                  key={option.value}
                  className="flex items-start gap-2 rounded-sm px-1 py-1 hover:bg-surface"
                >
                  <input
                    type="radio"
                    name="bi-dashboard-visibility"
                    checked={draft.visibility === option.value}
                    onChange={() =>
                      setDraft((current) => ({
                        ...current,
                        visibility: option.value,
                        visibilityRole:
                          option.value === "role"
                            ? (current.visibilityRole ?? ADDRESSABLE_ROLES[0])
                            : null,
                      }))
                    }
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="block text-body text-navy">
                      {option.label}
                    </span>
                    <span className="block text-caption text-slate">
                      {option.help}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            {draft.visibility === "role" && (
              <label className="flex max-w-xs flex-col gap-1.5">
                <span className="text-caption font-medium text-navy">
                  Which role
                </span>
                <select
                  value={draft.visibilityRole ?? ADDRESSABLE_ROLES[0]}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      visibilityRole: event.target.value,
                    }))
                  }
                  className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
                >
                  {ADDRESSABLE_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {addressableRoleLabel(role)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </fieldset>
        </div>
      </SectionCard>

      <SectionCard
        title="The views on it"
        subtitle="Drag a view by its grip to move it, pull its bottom-right corner to resize it, or set its columns and rows directly."
        actions={
          !composing && (
            <button
              type="button"
              onClick={() => {
                setEditingId(null);
                setComposing(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-caption font-medium text-action hover:bg-surface"
            >
              <Plus size={13} aria-hidden />
              Add a view
            </button>
          )
        }
      >
        <div className="space-y-4">
          {composing && (
            <div className="rounded-md border border-border bg-surface/50 p-4">
              <p className="mb-3 text-body font-medium text-navy">
                {editing ? `Change "${editing.title}"` : "Add a view"}
              </p>
              <WidgetComposer
                catalogue={catalogue}
                takenIds={draft.widgets.map((widget) => widget.id)}
                editing={editing}
                onSave={(widget: BiPackWidget) => {
                  setDraft((current) =>
                    editing
                      ? replaceWidget(current, widget)
                      : addWidget(current, widget),
                  );
                  setComposing(false);
                  setEditingId(null);
                }}
                onCancel={() => {
                  setComposing(false);
                  setEditingId(null);
                }}
              />
            </div>
          )}

          {draft.widgets.length === 0 ? (
            <p className="text-body leading-relaxed text-slate">
              Nothing is on the canvas yet. Add a view and it will be placed on
              its own row, half the width of the page; you can move and resize
              it from there.
            </p>
          ) : (
            <BuilderGrid
              layout={draft.layout}
              rowHeight={WIDGET_ROW_HEIGHT}
              items={items}
              onLayoutChange={onLayoutChange}
            />
          )}
        </div>
      </SectionCard>

      <SectionCard
        title={mode === "create" ? "Save it" : "Save this version"}
        subtitle={
          mode === "create"
            ? "It becomes yours: only you can change or delete it, however widely it is shared."
            : "Every save is kept. The previous version stays in the history and cannot be rewritten."
        }
      >
        <div className="space-y-3">
          {mode === "edit" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-caption font-medium text-navy">
                What changed
              </span>
              <input
                type="text"
                value={changeNote}
                onChange={(event) => setChangeNote(event.target.value)}
                maxLength={CHANGE_NOTE_MAX}
                placeholder="Optional — one line for the history"
                className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
              />
            </label>
          )}

          {problems.length > 0 && (
            <ul className="space-y-1">
              {problems.map((problem) => (
                <li key={problem} className="text-caption text-warning">
                  {problem}
                </li>
              ))}
            </ul>
          )}

          {Boolean(error) && (
            <ErrorPanel error={error} title="This dashboard was not saved" />
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={problems.length > 0 || saving}
              onClick={() => onSave(draft, changeNote.trim())}
              className="rounded-md bg-action px-4 py-2 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50"
            >
              {saving
                ? "Saving…"
                : mode === "create"
                  ? "Save dashboard"
                  : "Save new version"}
            </button>
            <Link
              href={cancelHref}
              className="rounded-md border border-border px-4 py-2 text-caption font-medium text-slate hover:bg-surface"
            >
              Cancel
            </Link>
          </div>
        </div>
      </SectionCard>
    </div>
  );
}
