import ModuleTabs from '@/components/shell/ModuleTabs';

// Governance → Regulatory Reporting hub (docs/regulatory_reporting.md §7).
// All official reporting lives here; module sub-navs link out to /returns.
// Tab order per Eric (2026-07-24): working surfaces first, Calendar as the
// planning view just before Settings. /submissions itself still lands on the
// Calendar (it is the hub index route); only the tab position changed.
const tabs = [
  { href: '/submissions/returns', label: 'Returns' },
  { href: '/submissions/approvals', label: 'Approvals' },
  { href: '/submissions/history', label: 'History' },
  { href: '/submissions/templates', label: 'Templates' },
  { href: '/submissions', label: 'Calendar' },
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
