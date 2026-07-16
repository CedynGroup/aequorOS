import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/fx', label: 'Exposure' },
  { href: '/fx/var', label: 'VaR & Stress' },
  { href: '/fx/hedges', label: 'Hedge Book' },
  { href: '/fx/limits', label: 'Limits' },
  { href: '/fx/forwards', label: 'Forwards' },
];

export default function FxLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
