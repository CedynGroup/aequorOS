import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/data-engine', label: 'Sources & Ingestion' },
  { href: '/data-engine/positions', label: 'Canonical Positions' },
];

export default function DataEngineLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
