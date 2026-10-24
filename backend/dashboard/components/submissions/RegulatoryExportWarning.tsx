import {
  packagePreparationWarning,
  type InstrumentDate,
} from "@/lib/regulatoryInstruments";

export default function RegulatoryExportWarning({
  snapshot,
  status,
  effectiveFrom,
}: {
  snapshot?: Record<string, unknown>;
  status?: string;
  effectiveFrom?: InstrumentDate;
}) {
  const warning = packagePreparationWarning(snapshot, status, effectiveFrom);
  return warning ? (
    <p role="note" className="text-caption text-warning">
      {warning}
    </p>
  ) : null;
}
