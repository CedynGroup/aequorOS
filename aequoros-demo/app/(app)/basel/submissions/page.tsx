import { FileSpreadsheet, FileText, Send } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StatusPill from '@/components/ui/StatusPill';
import { baselSubmissions } from '@/lib/data/basel';
import { bank } from '@/lib/data/bank';

const cols: Column<(typeof baselSubmissions)[number]>[] = [
  { key: 'reg', header: 'Regulator', render: (r) => r.regulator },
  {
    key: 'form',
    header: 'Form',
    render: (r) => <span className="font-mono text-caption">{r.form}</span>,
  },
  { key: 'name', header: 'Submission', render: (r) => r.name, width: '36%' },
  { key: 'freq', header: 'Frequency', render: (r) => r.frequency },
  { key: 'due', header: 'Due', numeric: true, render: (r) => r.due },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) => <StatusPill tone={r.tone}>{r.status}</StatusPill>,
  },
  {
    key: 'action',
    header: '',
    align: 'right',
    render: () => (
      <div className="flex items-center gap-1 justify-end">
        <button
          type="button"
          aria-label="Preview as PDF"
          className="w-7 h-7 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
        >
          <FileText size={13} />
        </button>
        <button
          type="button"
          aria-label="Export to regulator Excel"
          className="w-7 h-7 inline-flex items-center justify-center rounded text-slate hover:bg-surface"
        >
          <FileSpreadsheet size={13} />
        </button>
        <button
          type="button"
          aria-label="Submit"
          className="w-7 h-7 inline-flex items-center justify-center rounded text-action hover:bg-action-light"
        >
          <Send size={13} />
        </button>
      </div>
    ),
  },
];

export default function BaselSubmissions() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Submissions' },
        ]}
        title="Capital Submissions"
        subtitle="Multi-jurisdictional regulatory filings · BoG, CBN, SARB, CBK"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardHeader
            title="Active submissions"
            subtitle="Pre-formatted templates · Auto-populated from platform data"
          />
          <CardBody className="p-0">
            <DataTable columns={cols} rows={baselSubmissions} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Multi-jurisdiction support" subtitle="Capital frameworks supported by AequorOS" />
          <CardBody className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {[
              { jur: 'Bank of Ghana', framework: 'CRD 2018, ICAAP/ILAAP', minCar: '10.0%', dsib: '+1.0% if designated' },
              { jur: 'Central Bank of Nigeria', framework: 'CBN Basel III', minCar: '15.0%', dsib: '+1.0%/+1.5%/+2.0%' },
              { jur: 'South African Reserve Bank', framework: 'SARB Basel III', minCar: '11.5%', dsib: '+1.0%' },
              { jur: 'Central Bank of Kenya', framework: 'CBK Risk-Based Supervision', minCar: '14.5%', dsib: 'Pillar 2 add-on' },
            ].map((j) => (
              <div key={j.jur} className="border-l-4 border-l-action pl-4 space-y-1">
                <p className="text-body font-medium text-navy">{j.jur}</p>
                <p className="text-caption text-slate">{j.framework}</p>
                <p className="text-caption text-slate">
                  Min CAR <span className="font-mono text-navy">{j.minCar}</span>
                </p>
                <p className="text-caption text-slate">D-SIB: {j.dsib}</p>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
