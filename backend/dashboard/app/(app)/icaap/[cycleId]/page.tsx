import { preparationHref } from "@/lib/regulatoryInstruments";
import { redirect } from "next/navigation";

/** A cycle URL with no tab lands on the readiness overview. */
export default async function IcaapCycleIndex({
  params,
  searchParams,
}: {
  params: Promise<{ cycleId: string }>;
  searchParams: Promise<{ preparing?: string }>;
}) {
  const { cycleId } = await params;
  const { preparing } = await searchParams;
  redirect(preparationHref(`/icaap/${cycleId}/overview`, preparing === "1"));
}
