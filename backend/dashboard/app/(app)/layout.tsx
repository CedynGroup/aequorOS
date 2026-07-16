import AppShell from '@/components/shell/AppShell';
import BankProvider from '@/components/shell/BankContext';
import GuidedTour from '@/components/tour/GuidedTour';

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <BankProvider>
      <AppShell>{children}</AppShell>
      {/* Spotlight walkthrough over the shell — ?tour=1 or first-visit pill. */}
      <GuidedTour />
    </BankProvider>
  );
}
