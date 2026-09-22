import { redirect } from "next/navigation";

/** A cycle URL with no tab lands on the readiness overview. */
export default async function IcaapCycleIndex({
  params,
}: {
  params: Promise<{ cycleId: string }>;
}) {
  const { cycleId } = await params;
  redirect(`/icaap/${cycleId}/overview`);
}
