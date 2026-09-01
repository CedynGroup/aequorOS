import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/credit', label: 'Overview' },
  { href: '/credit/book', label: 'Loan Book' },
  { href: '/credit/concentration', label: 'Concentration' },
  { href: '/credit/activity', label: 'Activity' },
];

export default function CreditLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
