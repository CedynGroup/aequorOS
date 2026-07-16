import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/basel', label: 'Overview' },
  { href: '/basel/rwa', label: 'RWA' },
  { href: '/basel/structure', label: 'Capital Structure' },
  { href: '/basel/stress', label: 'Stress' },
  { href: '/basel/planning', label: 'Planning' },
];

export default function BaselLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
