import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/data-engine', label: 'Overview' },
  { href: '/data-engine/excel-csv', label: 'Excel & CSV' },
  { href: '/data-engine/api', label: 'API Push' },
  { href: '/data-engine/market-data', label: 'Market Data' },
  { href: '/data-engine/t24', label: 'Temenos T24' },
  { href: '/data-engine/database', label: 'Database (Direct)' },
  { href: '/data-engine/adapters', label: 'Other adapters' },
  { href: '/data-engine/positions', label: 'Canonical Data' },
];

export default function DataEngineLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
