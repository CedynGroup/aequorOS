import PageHeader from '@/components/ui/PageHeader';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { capitalStructure, capital } from '@/lib/data/basel';
import { bank } from '@/lib/data/bank';
import { fmtCurrencyFull } from '@/lib/format';

function TierBlock({
  title,
  tone,
  total,
  items,
  deductions,
  description,
}: {
  title: string;
  tone: string;
  total: number;
  items: { item: string; amountGHS: number }[];
  deductions: { item: string; amountGHS: number }[];
  description: string;
}) {
  return (
    <Card className={`border-l-4 border-l-${tone}`}>
      <CardHeader title={title} subtitle={description} />
      <CardBody className="p-0">
        <table className="w-full text-body">
          <tbody>
            {items.map((it) => (
              <tr key={it.item} className="border-b border-border-light">
                <td className="px-5 py-2.5 text-navy/85">{it.item}</td>
                <td className="px-5 py-2.5 num text-navy/90">
                  {fmtCurrencyFull(it.amountGHS, 'GHS')}
                </td>
              </tr>
            ))}
            {deductions.length > 0 &&
              deductions.map((d) => (
                <tr key={d.item} className="border-b border-border-light bg-critical-light/30">
                  <td className="px-5 py-2.5 text-critical text-caption">
                    Less: {d.item}
                  </td>
                  <td className="px-5 py-2.5 num text-critical">
                    {fmtCurrencyFull(d.amountGHS, 'GHS')}
                  </td>
                </tr>
              ))}
            <tr className="bg-surface font-medium border-t-2 border-navy">
              <td className="px-5 py-3 text-navy uppercase text-caption tracking-wider">
                {title} Total
              </td>
              <td className="px-5 py-3 num text-navy text-h3">
                {fmtCurrencyFull(total, 'GHS')}
              </td>
            </tr>
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

export default function CapitalStructurePage() {
  const totalCet1 = capitalStructure.cet1.total;
  const totalAt1 = capitalStructure.at1.total;
  const totalTier1 = totalCet1 + totalAt1;
  const totalTier2 = capitalStructure.tier2.total;
  const totalCapital = totalTier1 + totalTier2;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Capital Structure' },
        ]}
        title="Capital Structure"
        subtitle="Tier 1 (CET1, AT1), Tier 2, and regulatory deductions"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              CET1
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrencyFull(totalCet1, 'GHS')}
            </p>
            <p className="mt-1 text-caption text-slate">
              {((totalCet1 / capital.totalRwaGHS) * 100).toFixed(2)}% of RWA
            </p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Tier 1 (CET1 + AT1)
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrencyFull(totalTier1, 'GHS')}
            </p>
            <p className="mt-1 text-caption text-slate">
              {((totalTier1 / capital.totalRwaGHS) * 100).toFixed(2)}% of RWA
            </p>
          </div>
          <div className="card p-5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Total Capital
            </p>
            <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
              {fmtCurrencyFull(totalCapital, 'GHS')}
            </p>
            <p className="mt-1 text-caption text-slate">
              {((totalCapital / capital.totalRwaGHS) * 100).toFixed(2)}% of RWA · CAR
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <TierBlock
            title="CET1"
            tone="navy"
            total={capitalStructure.cet1.total}
            items={capitalStructure.cet1.items}
            deductions={capitalStructure.cet1.deductions}
            description="Common Equity Tier 1 — highest quality capital"
          />
          <TierBlock
            title="Additional Tier 1"
            tone="action"
            total={capitalStructure.at1.total}
            items={capitalStructure.at1.items}
            deductions={capitalStructure.at1.deductions}
            description="Going-concern capital · No instruments outstanding"
          />
          <TierBlock
            title="Tier 2"
            tone="teal"
            total={capitalStructure.tier2.total}
            items={capitalStructure.tier2.items}
            deductions={capitalStructure.tier2.deductions}
            description="Gone-concern capital · Sub-debt and qualifying reserves"
          />
        </div>
      </div>
    </>
  );
}
