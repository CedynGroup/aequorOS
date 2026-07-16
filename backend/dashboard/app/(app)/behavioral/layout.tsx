import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/behavioral/nmd-duration', label: 'NMD Duration' },
  { href: '/behavioral/prepayment', label: 'Prepayment' },
  { href: '/behavioral/deposit-stability', label: 'Deposit Stability' },
];

export default function BehavioralLayout({
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
