import {
  Button,
  Dialog,
  DialogContent,
  DialogTrigger,
} from "../../components/ui";
import type { TenantHeaders } from "../../lib/api";
import { BulkActionForm } from "./bulk-action-form";
export { BulkResult } from "./bulk-result";

export function BulkActionDialog({
  selectedIds,
  tenant,
}: {
  selectedIds: string[];
  tenant: TenantHeaders;
}) {
  const actionLabel =
    selectedIds.length === 0 ? "Select cases" : `Bulk actions (${selectedIds.length})`;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          disabled={selectedIds.length === 0}
          title={selectedIds.length === 0 ? "Select one or more queue rows to enable bulk actions." : undefined}
        >
          {actionLabel}
        </Button>
      </DialogTrigger>
      <DialogContent
        title="Bulk actions"
        description="Apply an assign, unassign, status update, or archive action to selected cases."
      >
        <BulkActionForm selectedIds={selectedIds} tenant={tenant} />
      </DialogContent>
    </Dialog>
  );
}
