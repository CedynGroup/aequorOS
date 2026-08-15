import OperatorsView from '@/components/admin/OperatorsView';

/**
 * /admin/operators — operator RBAC / staff-user management.
 *
 * Lists operator_users and drives create / reset-password / deactivate /
 * reactivate through the operator API (operator_admin-gated; 403-degrades to an
 * explainer). All logic lives in the client OperatorsView.
 */
export default function OperatorsPage() {
  return <OperatorsView />;
}
