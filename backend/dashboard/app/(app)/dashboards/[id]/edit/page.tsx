"use client";

/**
 * Editing a dashboard's definition.
 *
 * Two rules decide whether this route resolves, and together they refuse every
 * id this surface can be given today.
 *
 * ONLY THE OWNER EDITS. A saved dashboard belongs to the person who saved it;
 * nobody else may change or delete it, however widely it is shared. Sharing a
 * view is not sharing control of it.
 *
 * A CURATED PACK IS NEVER EDITED IN PLACE. It is certified content with a
 * version behind it, so an editable version of one is a COPY that becomes the
 * copier's own personal dashboard. Letting a reader rearrange the certified
 * original would leave two different things wearing one badge.
 *
 * The only dashboards bound to this surface are the platform's certified content
 * packs (`components/bi/dashboards.ts`), so the second rule refuses all of them
 * and this route is not found. That is the truthful answer, rather than an editor
 * with nowhere to save to. `components/bi/dashboards.ts::canEditDashboard` is
 * where the rule lives; the arrangement surface goes here in the same change that
 * binds the saved-dashboard routes and gives it somewhere to save.
 *
 * Nothing is fetched. Whether a view may be edited is a property of its STANDING,
 * not of its contents, so the refusal is reached without reading one figure.
 */

import { notFound } from "next/navigation";

export default function EditDashboardPage() {
  notFound();
}
