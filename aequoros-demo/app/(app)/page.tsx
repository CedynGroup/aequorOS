import Link from 'next/link';
import {
  ArrowRight,
  Sparkles,
  ChevronRight,
  CalendarClock,
  Clock,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { bank } from '@/lib/data/bank';
import {
  overviewKpis,
  upcomingDeadlines,
  recentActivity,
  aiInsights,
} from '@/lib/data/overview';
import { fmtCurrency } from '@/lib/format';

export default function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={`${bank.name} · Bank of Ghana licensee · ${bank.licenseClass}`}
        asOf={bank.asOf}
        action={
          <Link
            href="/submissions"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700"
          >
            Generate ALCO pack
            <ArrowRight size={13} aria-hidden />
          </Link>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {/* Bank profile bar */}
        <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-6">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Total assets
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {fmtCurrency(bank.totalAssetsGHS, 'GHS')}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Deposits
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {fmtCurrency(bank.totalDepositsGHS, 'GHS')}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Loans
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {fmtCurrency(bank.totalLoansGHS, 'GHS')}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Capital base
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {fmtCurrency(bank.capitalBaseGHS, 'GHS')}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Branches
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {bank.branches}
            </p>
          </div>
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              GHS / USD
            </p>
            <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
              {bank.ghsUsd.toFixed(2)}
            </p>
          </div>
        </div>

        {/* KPI grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {overviewKpis.map((k) => (
            <KPICard
              key={k.label}
              label={k.label}
              value={k.value}
              suffix={k.suffix}
              threshold={k.threshold}
              decimals={k.decimals}
              status={k.status as StatusTone}
              delta={k.delta}
              sparkline={k.sparkline}
              href={k.href}
              footer={k.sublabel}
            />
          ))}
        </div>

        {/* Two-column: AI insights + Deadlines */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2">
            <CardHeader
              title={
                <span className="inline-flex items-center gap-2">
                  <Sparkles size={15} className="text-action" aria-hidden />
                  AI insights
                </span>
              }
              subtitle="Generated overnight from the day’s recalculations"
              action={
                <Link
                  href="/liquidity/forecast"
                  className="text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1"
                >
                  View all <ChevronRight size={12} aria-hidden />
                </Link>
              }
            />
            <CardBody className="p-0">
              <ul className="divide-y divide-border-light">
                {aiInsights.map((i) => (
                  <li key={i.title} className="px-5 py-4">
                    <div className="flex items-start gap-4">
                      <div className="shrink-0 mt-0.5">
                        <StatusPill tone={i.severity as StatusTone}>
                          {i.module}
                        </StatusPill>
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-body font-medium text-navy">
                          {i.title}
                        </p>
                        <p className="mt-1 text-body text-slate leading-relaxed">
                          {i.body}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="text-micro font-medium uppercase tracking-wider text-slate">
                          Confidence
                        </p>
                        <p className="mt-0.5 font-mono text-body text-navy tabular-nums">
                          {(i.confidence * 100).toFixed(0)}%
                        </p>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={
                <span className="inline-flex items-center gap-2">
                  <CalendarClock size={15} className="text-warning" aria-hidden />
                  Upcoming filings
                </span>
              }
              subtitle="Bank of Ghana submissions"
            />
            <CardBody className="p-0">
              <ul className="divide-y divide-border-light">
                {upcomingDeadlines.map((d) => (
                  <li key={d.title} className="px-5 py-3.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-body font-medium text-navy truncate">
                          {d.title}
                        </p>
                        <p className="mt-0.5 text-caption text-slate">
                          Form {d.form} · Due{' '}
                          <span className="font-mono text-navy">{d.dueDate}</span>
                        </p>
                      </div>
                      <StatusPill tone={d.severity as StatusTone}>
                        {d.status}
                      </StatusPill>
                    </div>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </div>

        {/* Activity feed */}
        <Card>
          <CardHeader
            title={
              <span className="inline-flex items-center gap-2">
                <Clock size={15} className="text-slate" aria-hidden />
                Recent activity
              </span>
            }
            subtitle="System and user events across modules"
          />
          <CardBody className="p-0">
            <ul className="divide-y divide-border-light">
              {recentActivity.map((a, i) => (
                <li
                  key={i}
                  className="px-5 py-3 flex items-center gap-4 text-body"
                >
                  <span className="font-mono text-caption text-slate w-20 shrink-0">
                    {a.when}
                  </span>
                  <span className="font-medium text-navy w-36 shrink-0 truncate">
                    {a.actor}
                  </span>
                  <span className="text-navy/85 flex-1 min-w-0 truncate">
                    {a.event}
                  </span>
                  <span className="text-caption text-slate shrink-0">
                    {a.module}
                  </span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
