import { Download, FileSpreadsheet, Send, CheckCircle2 } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { submissionFormFields } from '@/lib/data/liquidity';
import { bank } from '@/lib/data/bank';
import { fmtCurrencyFull } from '@/lib/format';

export default function SubmissionPreview() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'BoG Submission' },
        ]}
        title="BoG LCR Submission — March 2026"
        subtitle="Form BSD/2/2024 · Liquidity Coverage Ratio Return"
        asOf={bank.asOf}
        action={
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
            >
              <Download size={13} aria-hidden /> Download PDF
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileSpreadsheet size={13} aria-hidden /> Export to BoG Excel
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-success rounded-md hover:bg-success/90"
            >
              <Send size={13} aria-hidden /> Submit to BoG portal
            </button>
          </div>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {/* Validation banner */}
        <div className="card border-l-4 border-l-success bg-success-light/40 p-5 flex items-start gap-3">
          <CheckCircle2 size={18} className="text-success shrink-0 mt-0.5" aria-hidden />
          <div>
            <p className="text-body font-medium text-navy">
              Validation passed · 28 of 28 BoG checks
            </p>
            <p className="mt-1 text-body text-navy/80 leading-relaxed">
              All line items reconcile to source ledger. HQLA haircuts applied
              per CRD Schedule 4. Net outflow calculation matches independent
              recompute. Filing ready for submission.
            </p>
            <p className="mt-2 text-caption text-slate">
              Reviewed by Akua Mensah · Approved by Yaa Adjei · 02 Apr 2026 09:42
            </p>
          </div>
        </div>

        {/* The "form" — looks like a regulatory document */}
        <div className="bg-white border border-border rounded-md shadow-subtle">
          {/* Form header */}
          <div className="px-8 py-6 border-b border-border-light">
            <div className="flex items-start justify-between gap-6 flex-wrap">
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  Bank of Ghana · Banking Supervision Department
                </p>
                <h2 className="mt-2 text-h1 text-navy">
                  Liquidity Coverage Ratio Return
                </h2>
                <p className="mt-1 text-body text-slate">
                  Form BSD/2/2024 · Effective 1 Jan 2024 · Reporting period: March 2026
                </p>
              </div>
              <div className="text-right text-caption text-slate space-y-1">
                <p>
                  Reporting bank{' '}
                  <span className="font-medium text-navy">{bank.name}</span>
                </p>
                <p>BoG license no. <span className="font-mono text-navy">UB-2005-018</span></p>
                <p>
                  Currency unit:{' '}
                  <span className="font-mono text-navy">GHS, full amounts</span>
                </p>
                <p>
                  Submitted on:{' '}
                  <span className="font-mono text-navy">02 Apr 2026</span>
                </p>
              </div>
            </div>
          </div>

          {/* Form body — looks like a real return */}
          <div className="px-8 py-6">
            <table className="w-full text-body border-collapse">
              <colgroup>
                <col style={{ width: '64px' }} />
                <col />
                <col style={{ width: '180px' }} />
              </colgroup>
              <thead>
                <tr className="border-b-2 border-navy">
                  <th className="text-left py-2 text-micro font-medium uppercase tracking-wider text-slate">
                    Row
                  </th>
                  <th className="text-left py-2 text-micro font-medium uppercase tracking-wider text-slate">
                    Description
                  </th>
                  <th className="text-right py-2 text-micro font-medium uppercase tracking-wider text-slate">
                    Amount (GHS)
                  </th>
                </tr>
              </thead>
              <tbody>
                {submissionFormFields.map((f) => {
                  const isTotal = f.item.startsWith('TOTAL');
                  const isRatio = f.isRatio;
                  return (
                    <tr
                      key={f.row}
                      className={`border-b ${
                        isTotal || isRatio
                          ? 'border-navy bg-surface font-medium'
                          : 'border-border-light'
                      }`}
                    >
                      <td className="py-2.5 font-mono text-caption text-slate align-top">
                        {f.row}
                      </td>
                      <td
                        className={`py-2.5 align-top ${
                          isTotal || isRatio
                            ? 'text-navy font-medium uppercase tracking-wide text-caption'
                            : 'text-navy/90'
                        }`}
                      >
                        {f.item}
                      </td>
                      <td className="py-2.5 num align-top">
                        {isRatio ? (
                          <span className="font-mono text-navy text-h3 tabular-nums">
                            {f.amount.toFixed(2)}%
                          </span>
                        ) : (
                          <span
                            className={`font-mono tabular-nums ${
                              isTotal ? 'text-navy text-h3' : 'text-navy/90'
                            }`}
                          >
                            {fmtCurrencyFull(f.amount, 'GHS')}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="px-8 py-5 border-t border-border-light bg-surface flex items-start justify-between flex-wrap gap-4">
            <div className="text-caption text-slate space-y-1 max-w-2xl">
              <p>
                <span className="font-medium text-navy">Certification:</span>{' '}
                The undersigned certifies that the information disclosed in this
                return is true, complete and prepared in accordance with Bank of
                Ghana directives and the Capital Requirements Directive.
              </p>
              <p>
                Prepared by: Akua Mensah, Head of Treasury &amp; ALM · Approved
                by: Yaa Adjei, Chief Risk Officer · Submitted by: AequorOS
                BoG-portal connector v2.4
              </p>
            </div>
            <StatusPill tone="success">Ready to submit</StatusPill>
          </div>
        </div>

        {/* Audit trail */}
        <Card>
          <CardHeader title="Audit trail" subtitle="Immutable record of changes to this return" />
          <CardBody className="p-0">
            <ul className="divide-y divide-border-light">
              <li className="px-5 py-3 flex items-center gap-4 text-caption">
                <span className="font-mono text-slate w-32 shrink-0">02 Apr 09:42</span>
                <span className="font-medium text-navy w-44 shrink-0">Yaa Adjei</span>
                <span className="text-navy/85 flex-1">Approved for submission</span>
                <StatusPill tone="success">Approved</StatusPill>
              </li>
              <li className="px-5 py-3 flex items-center gap-4 text-caption">
                <span className="font-mono text-slate w-32 shrink-0">02 Apr 08:18</span>
                <span className="font-medium text-navy w-44 shrink-0">Akua Mensah</span>
                <span className="text-navy/85 flex-1">Reviewed and submitted for CRO sign-off</span>
                <StatusPill tone="action">Reviewed</StatusPill>
              </li>
              <li className="px-5 py-3 flex items-center gap-4 text-caption">
                <span className="font-mono text-slate w-32 shrink-0">02 Apr 06:00</span>
                <span className="font-medium text-navy w-44 shrink-0">System</span>
                <span className="text-navy/85 flex-1">
                  Auto-generated from daily LCR run · 28 of 28 validation checks passed
                </span>
                <StatusPill tone="slate">Generated</StatusPill>
              </li>
            </ul>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
