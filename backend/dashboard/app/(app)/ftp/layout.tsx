import ModuleTabs from '@/components/shell/ModuleTabs';

const tabs = [
  { href: '/ftp', label: 'Curve' },
  { href: '/ftp/products', label: 'Product Profitability' },
  { href: '/ftp/lines', label: 'Business Lines' },
  { href: '/ftp/rules', label: 'Rules' },
  { href: '/ftp/expost', label: 'Ex-ante vs Ex-post' },
];

export default function FtpLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
