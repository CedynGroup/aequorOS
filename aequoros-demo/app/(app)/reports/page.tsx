import { FileBarChart2 } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody } from '@/components/ui/Card';
import StatusPill from '@/components/ui/StatusPill';
import { bank } from '@/lib/data/bank';

const reports = [
  { name: 'ALCO Quarterly Liquidity Report', module: 'Liquidity', author: 'Akua Mensah', date: '02 Apr 2026', status: 'success' as const, size: '14 pages' },
  { name: 'Q1 2026 ICAAP Stress Test Results', module: 'Basel', author: 'Yaa Adjei', date: '28 Mar 2026', status: 'success' as const, size: '42 pages' },
  { name: 'IRR Position Report — Mar 2026', module: 'IRR', author: 'System', date: '31 Mar 2026', status: 'success' as const, size: '8 pages' },
  { name: 'FX Exposure & Hedge Effectiveness', module: 'FX', author: 'Kojo Aboagye', date: '31 Mar 2026', status: 'success' as const, size: '12 pages' },
  { name: 'FTP Branch Profitability Q1', module: 'FTP', author: 'Akua Mensah', date: '15 Mar 2026', status: 'success' as const, size: '22 pages' },
  { name: 'Board Risk Committee Pack — March', module: 'Cross-module', author: 'Yaa Adjei', date: '08 Mar 2026', status: 'success' as const, size: '64 pages' },
  { name: 'Capital Plan FY2026', module: 'Forecasting', author: 'Akua Mensah', date: '12 Feb 2026', status: 'success' as const, size: '38 pages' },
  { name: 'Recovery & Resolution Plan', module: 'Cross-module', author: 'Yaa Adjei', date: '30 Jan 2026', status: 'success' as const, size: '86 pages' },
];

export default function ReportsPage() {
  return (
    <>
      <PageHeader
        title="Reports Library"
        subtitle="Cross-module reports, ALCO packs, board materials"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardBody className="p-0">
            <ul className="divide-y divide-border-light">
              {reports.map((r) => (
                <li
                  key={r.name}
                  className="px-5 py-3.5 flex items-center gap-4 hover:bg-surface-alt"
                >
                  <span className="w-9 h-9 rounded bg-action-light text-action inline-flex items-center justify-center shrink-0">
                    <FileBarChart2 size={16} aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-body font-medium text-navy truncate">
                      {r.name}
                    </p>
                    <p className="text-caption text-slate truncate">
                      {r.module} · {r.author} · {r.size}
                    </p>
                  </div>
                  <span className="font-mono text-caption text-slate w-28 shrink-0 tabular-nums">
                    {r.date}
                  </span>
                  <StatusPill tone={r.status}>Final</StatusPill>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
