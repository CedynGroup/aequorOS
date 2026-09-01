import { redirect } from 'next/navigation';

/**
 * Superseded by the Credit module (credit PR-2): the loan book is a
 * first-class module for both institution classes, not a capital sub-page.
 * The redirect keeps bookmarks and palette history working.
 */
export default function SdiLoanBookRedirect() {
  redirect('/credit');
}
