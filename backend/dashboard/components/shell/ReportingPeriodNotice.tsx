"use client";

import DataAccessNotice from "@/components/ui/DataAccessNotice";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBankContext } from "./BankContext";

export default function ReportingPeriodNotice() {
  const { periodsStatus, periodsError } = useBankContext();
  if (periodsStatus === "loading")
    return <p role="status">Loading reporting periods…</p>;
  if (periodsStatus === "denied")
    return (
      <DataAccessNotice requirement="DATA · Confidential · View for reporting periods" />
    );
  if (periodsStatus === "error")
    return (
      <ErrorPanel error={periodsError} title="Reporting periods unavailable" />
    );
  return null;
}
