import RegulatoryParametersView from '@/components/admin/RegulatoryParametersView';

/**
 * /admin/regulatory-parameters — the regulatory-parameter control plane
 * (docs/sdi.md §7 Phase C).
 *
 * Lists the global, class/type-keyed, effective-dated regulatory numbers (CAR
 * floors, exposure limits, paid-up floors, LMTD liquidity floors, provisioning
 * rates …) and drives propose / approve through the operator API under
 * four-eyes maker-checker (reads open to any operator; changes 403-degrade /
 * 422 four-eyes). All logic lives in the client RegulatoryParametersView.
 */
export default function RegulatoryParametersPage() {
  return <RegulatoryParametersView />;
}
