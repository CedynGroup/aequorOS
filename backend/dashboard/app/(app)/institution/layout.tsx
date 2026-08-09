import ModuleTabs from '@/components/shell/ModuleTabs';

// Governance → Institution Profile register — the corporate master-data
// mirror behind the LRT return family (profile, related parties, outlets,
// products & licences, name history), plus the Board Registers editor for
// the Board's adopted risk configuration (liquidity thresholds, EWI trigger
// levels, CRM haircuts, ECL assumptions). LRT packs generate from
// /submissions.
const tabs = [
  { href: '/institution', label: 'Profile' },
  { href: '/institution/parties', label: 'Related parties' },
  { href: '/institution/outlets', label: 'Outlets' },
  { href: '/institution/products', label: 'Products & licences' },
  { href: '/institution/history', label: 'Name history' },
  { href: '/institution/registers', label: 'Board Registers' },
];

export default function InstitutionLayout({
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
