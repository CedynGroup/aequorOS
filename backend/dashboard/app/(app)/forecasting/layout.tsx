import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/forecasting', label: 'Balance Sheet' },
  { href: '/forecasting/nii', label: 'NII Forecast' },
  { href: '/forecasting/scenario', label: 'Scenarios' },
  { href: '/forecasting/whatif', label: 'What-if Lab' },
  { href: '/forecasting/optimizer', label: 'Optimizer' },
  { href: '/forecasting/assumptions', label: 'Assumptions' },
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
