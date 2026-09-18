import PageHeader from "@/components/ui/PageHeader";
import AccessTabs from "@/components/access/AccessTabs";

export default function AccessLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <PageHeader
        title="Access control"
        subtitle="Members, sign-in, and machine access for your organization."
      />
      <div className="px-4 py-6 md:px-8">
        <AccessTabs />
        <div className="pt-6">{children}</div>
      </div>
    </>
  );
}
