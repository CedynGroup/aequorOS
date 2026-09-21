"use client";

/**
 * How this ICAAP quantifies interest-rate risk, and what that means.
 *
 * The register already prints the item. What it cannot print is the SHAPE of
 * the choice: there are two interest-rate methods, one supersedes the other on
 * a date the supervisor sets, and the standardised framework's own figures are
 * supervisory monitoring that reach a filing only through this register.
 *
 * WHETHER THE FRAMEWORK IS ALREADY REQUIRED. The card says so, and it never
 * works it out. The commencement date is a governed control-plane row, and the
 * register read resolves it against the framework's own `method_mandates`
 * declaration and hands this card the SERVER's sentence
 * (`pillar2_method_mandate`). A card that compared a date itself would either
 * alarm a preparer who is doing the right thing or reassure one who is not,
 * and it would be a second opinion that can drift from the one readiness and
 * the freeze preflight use. Where no statement arrives — a regime that
 * declares no mandate, or a payload that did not answer, which the wire cannot
 * tell apart — the card says the position is not stated here. Never "not
 * required": that is the reassurance a preparer would act on.
 *
 * A REFUSAL IS CARRIED, NOT SMOOTHED. When the framework refused to measure
 * the book, the register item reports no amount — and the server's own
 * sentence explaining why is shown here rather than left as an absent figure
 * a reader would take for a zero.
 */

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import type { IcaapPillar2Register } from "@/lib/api/icaapRiskCapital";
import { ICON_XS } from "./display";
import {
  IRRBB_SF_ABSENT,
  IRRBB_SF_INTERIM_IN_USE,
  IRRBB_SF_IN_USE,
  IRRBB_SF_MANDATE_IS_GOVERNED,
  IRRBB_SF_MANDATE_NOT_STATED,
  IRRBB_SF_OPEN_WORKSPACE,
  IRRBB_SF_SUPERVISORY_ONLY,
  MANDATE_FINDING_CODE,
  METHOD_IRRBB_INTERIM,
  METHOD_IRRBB_SF,
  fmtAmount,
  itemStatusCopy,
  notComputableCopy,
} from "./labels";

const IRRBB_METHODS = new Set([METHOD_IRRBB_SF, METHOD_IRRBB_INTERIM]);

export default function IrrbbSfCard({
  register,
}: {
  register: IcaapPillar2Register;
}) {
  // Belt to the adapter's braces: the hooks make the normaliser a required
  // argument, so a mounted card's register always satisfies its type — but a
  // card that throws takes the whole Pillar 2 tab down with it.
  const rows = Array.isArray(register?.items) ? register.items : [];
  const items = rows.filter((item) => IRRBB_METHODS.has(item.method));
  const findings = Array.isArray(register?.findings) ? register.findings : [];
  const mandate = findings.find(
    (finding) =>
      finding.code === MANDATE_FINDING_CODE &&
      finding.params?.method === METHOD_IRRBB_SF,
  );
  // Printed as the server wrote it. An empty sentence is treated as no
  // sentence, so a payload that is vague never becomes a reassurance.
  const mandateStatement = mandate?.params?.statement ?? "";
  const framework = items.find((item) => item.method === METHOD_IRRBB_SF);
  const interim = items.find((item) => item.method === METHOD_IRRBB_INTERIM);
  const inUse = framework ?? interim ?? null;

  const lead =
    framework !== undefined
      ? IRRBB_SF_IN_USE
      : interim !== undefined
        ? IRRBB_SF_INTERIM_IN_USE
        : IRRBB_SF_ABSENT;

  return (
    <SectionCard
      title="Interest-rate risk in the banking book"
      subtitle={IRRBB_SF_SUPERVISORY_ONLY}
      actions={
        <Link
          href="/irr/standardised"
          className="inline-flex items-center gap-1 text-caption text-action underline-offset-2 hover:underline"
        >
          {IRRBB_SF_OPEN_WORKSPACE}
          <ArrowUpRight size={ICON_XS} aria-hidden />
        </Link>
      }
    >
      <p className="text-body leading-relaxed text-navy/80">{lead}</p>

      <div className="mt-3 rounded border border-border-light bg-surface/60 p-3">
        <p className="text-caption font-medium text-navy">
          Whether the standardised framework is required
        </p>
        <p className="mt-1 text-body leading-relaxed text-navy/80">
          {mandateStatement === ""
            ? IRRBB_SF_MANDATE_NOT_STATED
            : mandateStatement}
        </p>
        {mandateStatement === "" ? null : (
          <p className="mt-1 text-caption text-slate">
            {IRRBB_SF_MANDATE_IS_GOVERNED}
          </p>
        )}
      </div>

      {inUse === null ? null : (
        <div className="mt-3 rounded border border-border-light p-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-medium text-navy">{inUse.title}</p>
              <p className="text-caption text-slate">{inUse.methodLabel}</p>
            </div>
            <div className="flex shrink-0 items-start gap-4">
              <div className="text-right">
                <p className="text-caption text-slate">Baseline</p>
                <p className="tnum text-navy">
                  {fmtAmount(inUse.baselineAmount)}
                </p>
              </div>
              <StatusPill tone={itemStatusCopy(inUse.methodStatus).tone}>
                {itemStatusCopy(inUse.methodStatus).label}
              </StatusPill>
            </div>
          </div>
          {inUse.statusDetail === null ? null : (
            <p className="mt-2 text-caption leading-relaxed text-warning">
              {notComputableCopy(inUse.statusDetail)}
            </p>
          )}
          {inUse.representativeParameters.length === 0 ? null : (
            <p className="mt-2 text-caption leading-relaxed text-slate">
              Representative calibrations this figure rests on:{" "}
              {inUse.representativeParameters.join(", ")}. Confirm them with
              your supervisor before the figure is relied on.
            </p>
          )}
        </div>
      )}
    </SectionCard>
  );
}
