import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/liquidity', label: 'LCR Dashboard' },
  { href: '/liquidity/nsfr', label: 'NSFR Dashboard' },
  { href: '/liquidity/forecast', label: 'Cash Flow Forecast' },
  { href: '/liquidity/stress', label: 'Stress Scenarios' },
  { href: '/liquidity/submission', label: 'BoG Submission' },
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
