import ModuleTabs from '@/components/shell/ModuleTabs';

// Gap-ladder note: the LCR engine materializes category-level line items
// (runoff-weighted outflows / inflows over a single 30-day stressed horizon),
// not maturity-bucketed flows, so there is no dedicated Gap Ladder tab — the
// outflow/inflow decomposition lives on the Cockpit instead.
const tabs = [
  { href: '/liquidity', label: 'Cockpit' },
  { href: '/liquidity/buffer', label: 'Buffer' },
  { href: '/liquidity/nsfr', label: 'NSFR' },
  { href: '/liquidity/forecast', label: 'Cash Flow Forecast' },
  { href: '/liquidity/stress', label: 'Stress' },
  { href: '/liquidity/cfp', label: 'CFP' },
];

export default function LiquidityLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
