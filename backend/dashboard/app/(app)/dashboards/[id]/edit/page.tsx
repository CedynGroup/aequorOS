"use client";

/**
 * Editing a dashboard's definition.
 *
 * Two rules decide whether this route resolves, and together they refuse every
 * id today.
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
 * No dashboard has an owner yet, because no dashboard source is published yet
 * (`components/bi/dashboards.ts`), so `canEditDashboard` is false for every id
 * and this route is not found — the truthful answer, rather than an editor with
 * nowhere to save to. The arrangement surface goes in the branch below, in the
 * same change that gives it somewhere to save.
 */

import { use } from "react";
import { notFound, redirect } from "next/navigation";
import { canEditDashboard, findDashboard } from "@/components/bi/dashboards";

export default function EditDashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  if (canEditDashboard(findDashboard(id))) {
    redirect(`/dashboards/${id}`);
  }
  notFound();
}
