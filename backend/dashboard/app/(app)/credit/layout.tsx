import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/credit', label: 'Overview' },
  { href: '/credit/book', label: 'Loan Book' },
  { href: '/credit/delinquency', label: 'Delinquency & Migration' },
  { href: '/credit/concentration', label: 'Concentration' },
  { href: '/credit/vintages', label: 'Vintages' },
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
