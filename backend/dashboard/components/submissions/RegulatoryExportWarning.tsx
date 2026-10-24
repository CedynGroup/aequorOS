import {
  packagePreparationWarning,
  type InstrumentDate,
} from "@/lib/regulatoryInstruments";

export default function RegulatoryExportWarning({
  snapshot,
  status,
  effectiveFrom,
  regulator,
}: {
  snapshot?: Record<string, unknown>;
  status?: string;
  effectiveFrom?: InstrumentDate;
  regulator?: string;
}) {
  const warning = packagePreparationWarning(
    snapshot,
    status,
    effectiveFrom,
    regulator,
  );
  return warning ? (
    <p role="note" className="text-caption text-warning">
      {warning}
    </p>
  ) : null;
}
