'use client';

/**
 * Regulatory pack cards for the Reports library: the two Bank of Ghana
 * returns (BSD-2 capital, BSD-3 liquidity) linking to their module-owned
 * submission pages, plus the print-optimized Board Pack composite owned by
 * this module. Readiness comes from the same BSD preview hooks the module
 * pages use — this card deck never rebuilds the returns themselves.
 */

import Link from 'next/link';
import { ChevronRight, FileCheck2, Presentation } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useBsd2Preview,
  useBsd3Preview,
} from '@/lib/api/hooks';
import { shortId } from '@/lib/api/values';

type PackQuery = {
  isLoading: boolean;
  error: unknown;
  data: { runId: string } | undefined;
};

function packStatus(query: PackQuery): {
  tone: StatusTone;
  label: string;
  ready: boolean;
} {
  if (query.isLoading) return { tone: 'slate', label: 'Checking…', ready: false };
  if (query.data) return { tone: 'success', label: 'Ready', ready: true };
  if (isNoBaselineRunError(query.error)) {
    return { tone: 'amber', label: 'Baseline run required', ready: false };
  }
  if (query.error) return { tone: 'slate', label: 'Unavailable', ready: false };
  return { tone: 'slate', label: 'Unavailable', ready: false };
}

export default function PackCards({
  bankId,
  periodId,
}: {
  bankId: string | undefined;
  periodId: string | undefined;
}) {
  const { period } = useBankContext();
  const bsd2 = useBsd2Preview(bankId, periodId);
  const bsd3 = useBsd3Preview(bankId, periodId);

  const packs: {
    form: string;
    title: string;
    description: string;
    href: string;
    status: { tone: StatusTone; label: string; ready: boolean };
    runId?: string;
  }[] = [
    {
      form: 'BSD-2',
      title: 'BoG Capital Adequacy Return',
      description:
        'Capital structure, risk-weighted assets, and capital ratios — generated from the latest successful baseline capital run on the Basel module.',
      href: '/basel/submissions',
      status: packStatus(bsd2),
      runId: bsd2.data?.runId,
    },
    {
      form: 'BSD-3',
      title: 'BoG Liquidity Return (LCR & NSFR)',
      description:
        'Liquidity Coverage Ratio and Net Stable Funding Ratio — generated from the latest successful baseline liquidity run on the Liquidity module.',
      href: '/liquidity/submission',
      status: packStatus(bsd3),
      runId: bsd3.data?.runId,
    },
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {packs.map((pack) => (
        <Card key={pack.form}>
          <CardHeader
            title={
              <span className="inline-flex items-center gap-2">
                <FileCheck2 size={15} className="text-action" aria-hidden />
                {pack.form}
              </span>
            }
            subtitle={period ? `Period ${period.label}` : undefined}
            action={
              <StatusPill tone={pack.status.tone}>
                {pack.status.label}
              </StatusPill>
            }
          />
          <CardBody>
            <p className="text-body font-medium text-navy">{pack.title}</p>
            <p className="mt-1.5 text-body text-slate leading-relaxed">
              {pack.description}
            </p>
            <div className="mt-4 flex items-center justify-between gap-3">
              <Link
                href={pack.href}
                className="inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
              >
                {pack.status.ready ? 'View return preview' : 'Open module page'}
                <ChevronRight size={12} aria-hidden />
              </Link>
              {pack.runId && (
                <span
                  className="font-mono text-[10px] text-slate tnum"
                  title={`Source run ${pack.runId}`}
                >
                  run {shortId(pack.runId, 8)}
                </span>
              )}
            </div>
          </CardBody>
        </Card>
      ))}

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-2">
              <Presentation size={15} className="text-action" aria-hidden />
              Board Pack
            </span>
          }
          subtitle={period ? `Period ${period.label}` : undefined}
          action={<StatusPill tone="action">Print-ready</StatusPill>}
        />
        <CardBody>
          <p className="text-body font-medium text-navy">
            Executive board pack (A4)
          </p>
          <p className="mt-1.5 text-body text-slate leading-relaxed">
            Cover page, cross-module executive summary, and one-page module
            briefs with provenance — composed from live figures and formatted
            for print or PDF export.
          </p>
          <div className="mt-4">
            <Link
              href="/reports/board-pack"
              className="inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
            >
              Open board pack
              <ChevronRight size={12} aria-hidden />
            </Link>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
