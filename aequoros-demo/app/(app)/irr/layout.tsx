import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/irr', label: 'Dashboard' },
  { href: '/irr/scenarios', label: 'Rate Scenarios' },
  { href: '/irr/positions', label: 'Position Viewer' },
  { href: '/irr/hedging', label: 'AI Hedging' },
];

export default function IRRLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
