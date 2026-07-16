import ModuleTabs from '@/components/shell/ModuleTabs';

// Governance → Regulatory Reporting hub (docs/regulatory_reporting.md §7).
// All official reporting lives here; module sub-navs link out to /returns.
const tabs = [
  { href: '/submissions', label: 'Calendar' },
  { href: '/submissions/returns', label: 'Returns' },
  { href: '/submissions/approvals', label: 'Approvals' },
  { href: '/submissions/history', label: 'History' },
  { href: '/submissions/templates', label: 'Templates' },
  { href: '/submissions/settings', label: 'Settings' },
];

export default function SubmissionsLayout({
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
