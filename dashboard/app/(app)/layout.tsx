import AppShell from '@/components/shell/AppShell';
import BankProvider from '@/components/shell/BankContext';

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <BankProvider>
      <AppShell>{children}</AppShell>
    </BankProvider>
  );
}
