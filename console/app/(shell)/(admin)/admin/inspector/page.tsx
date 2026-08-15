import InspectorView from '@/components/admin/InspectorView';

/**
 * /admin/inspector — Tenant Inspector.
 *
 * Read-only, audited, time-boxed cross-tenant inspection (NOT act-as-user).
 * Starts / lists / ends sessions and drives the shell's un-dismissable
 * ImpersonationBanner via useInspector().setActive. Logic lives in the client
 * InspectorView.
 */
export default function InspectorPage() {
  return <InspectorView />;
}
