"use client";

/**
 * The dashboards this client can open.
 *
 * There are none yet, and that is a statement about the platform rather than a
 * placeholder. Two sources will fill this list and neither exists on the wire
 * today: the curated content packs, which are authored server-side as
 * `app/domain/bi/packs/<id>.json` and validated by `PackSpec`, and saved
 * dashboards, which are tenant rows with an owner, a visibility and a version
 * history. Both arrive as payloads that parse into `BiDashboard`, which is why
 * that type — and `DashboardCanvas`, which renders it — are already the shape
 * this module hands out.
 *
 * Until then `/dashboards` shows its real empty state and a dashboard URL
 * resolves to nothing, which is the truth: no dashboard exists to open.
 *
 * When the pack route lands, replace the constant with the hook that reads it.
 * Do not hard-code a pack here: a pack binds widgets to catalogue measures and
 * is validated server-side, and a copy in the browser is a second definition
 * free to disagree with the one the exports and the AI commentary read.
 */

import type { BiDashboard } from "./types";

const DASHBOARDS: readonly BiDashboard[] = [];

export function listDashboards(): readonly BiDashboard[] {
  return DASHBOARDS;
}

export function findDashboard(id: string): BiDashboard | null {
  return DASHBOARDS.find((dashboard) => dashboard.id === id) ?? null;
}

/**
 * Whether this reader may change a dashboard's definition.
 *
 * Only a saved dashboard's own owner may edit or delete it, and a curated pack
 * is never editable in place — an editable copy is a copy. No dashboard has an
 * owner yet, so nothing is editable yet.
 */
export function canEditDashboard(dashboard: BiDashboard | null): boolean {
  return dashboard !== null && dashboard.certification === "personal";
}

export const CERTIFICATION_LABELS = {
  platform: "Platform-certified",
  bank: "Bank-certified",
  personal: "Personal",
} as const;
