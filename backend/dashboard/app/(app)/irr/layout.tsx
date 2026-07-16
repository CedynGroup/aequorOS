import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/irr', label: 'Overview' },
  { href: '/irr/sensitivity', label: 'EVE & NII' },
  { href: '/irr/gaps', label: 'Gap Analysis' },
  { href: '/irr/scenarios', label: 'Scenarios' },
  { href: '/irr/limits', label: 'Limits' },
];

export default function IrrLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
