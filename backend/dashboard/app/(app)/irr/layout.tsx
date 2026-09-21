import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/irr', label: 'Overview' },
  { href: '/irr/sensitivity', label: 'EVE & NII' },
  { href: '/irr/gaps', label: 'Gap Analysis' },
  { href: '/irr/scenarios', label: 'Scenarios' },
  { href: '/irr/limits', label: 'Limits' },
  // The prescribed standardised framework. A separate tab, not a mode of the
  // others: it is a different scenario set with its own outlier test, and the
  // legacy engine behind the tabs above is untouched by it.
  { href: '/irr/standardised', label: 'Standardised Framework' },
];

export default function IrrLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
