import { fmtDateUTC, fmtRelative, fmtTimestamp } from "@/lib/api/values";

/**
 * Desk-grade provenance line: which engine tier produced the figures on
 * screen and how fresh they are. Stored/live status without any governance
 * vocabulary — full run provenance lives on the Reports registry.
 */
export default function LiveEngineNote({
  live,
  stored,
  dataAsOf,
}: {
  live?: { status: string; computedAt: Date } | null;
  stored: boolean;
  dataAsOf?: Date;
}) {
  if (live) {
    const healthy = live.status.toLowerCase() === "green";
    return (
      <span
        className="text-caption text-slate whitespace-normal text-right"
        title={`Calculation time, not the reporting date. Engines last ran ${fmtRelative(live.computedAt)}.`}
      >
        {dataAsOf && <>Dashboard reporting date: {fmtDateUTC(dataAsOf)}. </>}
        Calculated {fmtTimestamp(live.computedAt)}
        {!healthy && (
          <>
            {" "}
            <span className="font-mono text-micro uppercase">
              {live.status}
            </span>
          </>
        )}
      </span>
    );
  }
  return (
    <span className="text-caption text-slate whitespace-normal text-right">
      {dataAsOf && <>Dashboard reporting date: {fmtDateUTC(dataAsOf)}. </>}
      {stored
        ? "Computed from stored engine results"
        : "Computed live from current positions"}
    </span>
  );
}
