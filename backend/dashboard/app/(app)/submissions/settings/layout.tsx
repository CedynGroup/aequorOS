import PageContainer from "@/components/ui/PageContainer";
import { SettingsNav } from "@/components/submissions/settings/SettingsChrome";

export default function ReportingSettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <PageContainer className="py-6">
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <SettingsNav />
        <div className="min-w-0 space-y-6">{children}</div>
      </div>
    </PageContainer>
  );
}
