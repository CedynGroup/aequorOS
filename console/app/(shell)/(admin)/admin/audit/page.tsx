import AuditView from '@/components/admin/AuditView';

/**
 * /admin/audit — the append-only operator_audit_log viewer.
 *
 * Filterable (operator / target org / action prefix / date range), paginated
 * against total, row → detail drawer, client-side CSV export of the loaded
 * rows. operator_admin-gated (403-degrades). Logic lives in the client
 * AuditView.
 */
export default function AuditPage() {
  return <AuditView />;
}
