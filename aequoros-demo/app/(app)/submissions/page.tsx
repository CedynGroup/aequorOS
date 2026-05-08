import Link from 'next/link';
import { FileCheck2 } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { bank } from '@/lib/data/bank';

const upcoming = [
  { regulator: 'Bank of Ghana', form: 'BSD-2', name: 'Monthly Prudential Return', due: '10 Apr 2026', status: 'In review', tone: 'amber', href: '/submissions' },
  { regulator: 'Bank of Ghana', form: 'LCR-1', name: 'Liquidity Coverage Ratio Return', due: '10 Apr 2026', status: 'Ready to submit', tone: 'success', href: '/liquidity/submission' },
  { regulator: 'Bank of Ghana', form: 'CAR-Q', name: 'Capital Adequacy Return — Q1 2026', due: '15 Apr 2026', status: 'Pending data', tone: 'amber', href: '/basel' },
  { regulator: 'Bank of Ghana', form: 'NSFR-1', name: 'Net Stable Funding Ratio Return', due: '20 Apr 2026', status: 'Drafting', tone: 'slate', href: '/liquidity/nsfr' },
  { regulator: 'Bank of Ghana', form: 'ICAAP', name: 'ICAAP Internal Submission — H1', due: '30 Jun 2026', status: 'Drafting', tone: 'slate', href: '/basel' },
];

const recent = [
  { regulator: 'Bank of Ghana', form: 'BSD-2', name: 'Monthly Prudential Return — Feb', submitted: '08 Mar 2026', tone: 'success' },
  { regulator: 'Bank of Ghana', form: 'LCR-1', name: 'LCR Return — Feb 2026', submitted: '08 Mar 2026', tone: 'success' },
  { regulator: 'Bank of Ghana', form: 'CAR-Q', name: 'Capital Adequacy Return — Q4 2025', submitted: '14 Jan 2026', tone: 'success' },
  { regulator: 'Bank of Ghana', form: 'AML-1', name: 'AML Quarterly Return — Q4 2025', submitted: '11 Jan 2026', tone: 'success' },
];

export default function SubmissionsPage() {
  return (
    <>
      <PageHeader
        title="Regulatory Submissions"
        subtitle="Bank of Ghana filings · CBN, SARB, CBK supported"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardHeader title="Upcoming submissions" subtitle="Next 90 days" />
          <CardBody className="p-0">
            <table className="w-full text-body">
              <thead>
                <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                  <th className="text-left px-5 py-2.5">Regulator</th>
                  <th className="text-left px-5 py-2.5">Form</th>
                  <th className="text-left px-5 py-2.5">Submission</th>
                  <th className="text-right px-5 py-2.5">Due</th>
                  <th className="text-right px-5 py-2.5 pr-5">Status</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((u) => (
                  <tr key={u.name} className="border-b border-border-light last:border-b-0 hover:bg-surface-alt">
                    <td className="px-5 py-3 text-navy/85">{u.regulator}</td>
                    <td className="px-5 py-3 font-mono text-caption text-slate">{u.form}</td>
                    <td className="px-5 py-3">
                      <Link href={u.href} className="text-action hover:text-action-hover font-medium">
                        {u.name}
                      </Link>
                    </td>
                    <td className="px-5 py-3 num">{u.due}</td>
                    <td className="px-5 py-3 text-right">
                      <StatusPill tone={u.tone as StatusTone}>{u.status}</StatusPill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Recent submissions" subtitle="Submitted and acknowledged" />
          <CardBody className="p-0">
            <ul className="divide-y divide-border-light">
              {recent.map((r) => (
                <li key={r.name} className="px-5 py-3 flex items-center gap-4">
                  <FileCheck2 size={16} className="text-success shrink-0" aria-hidden />
                  <span className="font-mono text-caption text-slate w-16 shrink-0">
                    {r.form}
                  </span>
                  <span className="text-body text-navy/85 flex-1 truncate">
                    {r.name}
                  </span>
                  <span className="font-mono text-caption text-slate w-28 shrink-0 tabular-nums">
                    {r.submitted}
                  </span>
                  <StatusPill tone={r.tone as StatusTone}>Acknowledged</StatusPill>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
