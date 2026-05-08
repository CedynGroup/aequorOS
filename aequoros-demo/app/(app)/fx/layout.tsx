import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/fx', label: 'Exposure Dashboard' },
  { href: '/fx/scenarios', label: 'Currency Scenarios' },
  { href: '/fx/prediction', label: 'Rate Prediction' },
  { href: '/fx/hedging', label: 'Hedging Dashboard' },
];

export default function FXLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
