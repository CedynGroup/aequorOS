"use client";

/**
 * One dashboard, read — and there are two kinds of them behind one address.
 *
 * WHICH ONE IS DECIDED BY THE ROUTES' OWN PATH TYPES, not by guessing at content.
 * A saved dashboard's id is the UUID `GET …/bi/dashboards/{dashboard_id}`
 * declares; a certified pack's key is the lower-case slug `GET …/bi/packs/{pack}`
 * declares. `isSavedDashboardId` reads the shape and exactly one route is asked,
 * because asking the other would answer 404 for a document that exists.
 *
 * Both screens render through the SAME canvas and the SAME widget adapter, which
 * is what makes the disclosure property hold for a shared saved dashboard without
 * a second implementation of it: a refused view arrives with its geometry and
 * nothing else, and the view type it lands in has no field for anything more.
 */

import { use } from "react";
import CertifiedPackScreen from "@/components/bi/CertifiedPackScreen";
import SavedDashboardScreen from "@/components/bi/SavedDashboardScreen";
import { isSavedDashboardId } from "@/components/bi/builder";

export default function DashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return isSavedDashboardId(id) ? (
    <SavedDashboardScreen id={id} />
  ) : (
    <CertifiedPackScreen id={id} />
  );
}
