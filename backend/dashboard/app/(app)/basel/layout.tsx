import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/basel', label: 'Capital Dashboard' },
  { href: '/basel/rwa', label: 'RWA Breakdown' },
  { href: '/basel/structure', label: 'Capital Structure' },
  { href: '/basel/stress', label: 'Stress Testing' },
  { href: '/basel/submissions', label: 'Submissions' },
];

export default function BaselLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
