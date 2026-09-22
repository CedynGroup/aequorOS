import { Card, CardBody, CardHeader } from "@/components/ui/Card";

export default function MyAccessPage() {
  return (
    <Card>
      <CardHeader
        title="My access"
        subtitle="Your effective permissions and grant history."
      />
      <CardBody>
        <p className="text-body text-slate">
          Your access summary will appear here. In the meantime, module pages
          name the exact permission to request when access is missing.
        </p>
      </CardBody>
    </Card>
  );
}
