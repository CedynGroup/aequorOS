import { redirect } from "next/navigation";

/** A cycle URL with no tab lands on the readiness overview. */
export default function IcaapCycleIndex({
  params,
}: {
  params: { cycleId: string };
}) {
  redirect(`/icaap/${params.cycleId}/overview`);
}
