import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/ftp', label: 'Yield Curve' },
  { href: '/ftp/branches', label: 'Branch P&L' },
  { href: '/ftp/products', label: 'Product P&L' },
  { href: '/ftp/rates', label: 'FTP Rates' },
];

export default function FTPLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
