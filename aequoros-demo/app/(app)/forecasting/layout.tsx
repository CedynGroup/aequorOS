import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/forecasting', label: 'Forecast Dashboard' },
  { href: '/forecasting/scenario', label: 'Scenario Builder' },
  { href: '/forecasting/optimizer', label: 'Strategy Optimizer' },
  { href: '/forecasting/whatif', label: 'What-if Analysis' },
];

export default function ForecastingLayout({
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
