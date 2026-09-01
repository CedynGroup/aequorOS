import { redirect } from 'next/navigation';

/**
 * Superseded by the Credit module's concentration monitor (credit PR-3):
 * single-obligor exposure lives with the other concentration dimensions now.
 */
export default function SdiExposuresRedirect() {
  redirect('/credit/concentration');
}
