/**
 * Driving the signing ceremony through the real UI.
 *
 * Signing is required for every return by default, so any journey that reaches a
 * submission channel has to pass through the ceremony. There are only two honest
 * ways to do that, and relaxing the signing policy is not one of them — it would
 * delete coverage of the exact gate the ceremony exists to be. So these helpers
 * do it the way an officer does: place the fields, adopt a mark, prove presence
 * with a password, certify.
 *
 * The password half is possible because `scripts/e2e_bootstrap.py` gives every
 * fixture user a password hash (see `E2E_PASSWORD`). Without it `verify_step_up`
 * has nothing to check and the whole chain past validation is unreachable in a
 * browser.
 *
 * Shared by the attestation spec and the full-lifecycle journeys so a change to
 * the ceremony surface breaks both, rather than being fixed in one and rotting in
 * the other.
 */

import { expect, type Browser, type Locator, type Page } from '@playwright/test';
import { E2E_PASSWORD } from './mint';

/** How long a ceremony may take: pyHanko signs and re-renders the document. */
const CEREMONY_TIMEOUT = 120_000;
/** pdf.js has to render the page before anything can be placed on it. */
const RENDER_TIMEOUT = 60_000;

export const returnsUrl = (code: string, date?: string): string =>
  `/submissions/returns?code=${encodeURIComponent(code)}${date ? `&date=${date}` : ''}`;

/** Mirror of the dashboard's fmtDateUTC ("2026-02-28" → "28 Feb 2026"). */
export function fmtDateGB(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

/**
 * Drop a field of `fieldType` for the currently selected recipient.
 *
 * Click-to-arm then click-to-place, rather than an HTML5 drag: it is the
 * keyboard-reachable half of the same gesture, it exercises exactly the same
 * placement path, and it does not depend on the browser's drag emulation.
 */
export async function placeField(
  workspace: Locator,
  fieldType: string,
  at: { x: number; y: number },
): Promise<void> {
  await workspace
    .getByTestId('field-palette')
    .locator(`[data-field-type="${fieldType}"]`)
    .click();
  await workspace.getByTestId('placement-surface').click({ position: at });
}

/**
 * Both signature fields, on the page, so the ceremony is unblocked.
 *
 * The workspace opens EMPTY on purpose — the platform default puts boxes near
 * the attestation block rather than on its ruled lines — so every journey that
 * wants to reach the step-up has to place them first, exactly as a signer does.
 * The preparer places the APPROVER's box too: the certification permits no new
 * field afterwards.
 */
export async function placeBothSignatureFields(
  page: Page,
  workspace: Locator,
): Promise<void> {
  await expect(workspace.locator('canvas[aria-label^="Return document"]')).toBeVisible({
    timeout: RENDER_TIMEOUT,
  });
  await expect(workspace.getByTestId('placement-surface')).toBeVisible({
    timeout: RENDER_TIMEOUT,
  });
  await placeField(workspace, 'signature', { x: 120, y: 500 });
  await workspace.getByTestId('field-palette').locator('[data-recipient-role="approver"]').click();
  await placeField(workspace, 'signature', { x: 380, y: 500 });
  await expect(page.getByText(/Place a signature field for/i)).toHaveCount(0);
}

/**
 * Adopt a typed mark, once per signer.
 *
 * The mark persists on the signer's identity, so a second journey finds it
 * already adopted — asserting it is there either way keeps the helper honest
 * about what it guaranteed.
 */
export async function adoptTypedMark(workspace: Locator): Promise<void> {
  const adopted = workspace.getByText('Adopted', { exact: true });
  if ((await adopted.count()) === 0) {
    await workspace.getByRole('tab', { name: 'Type it' }).click();
    await workspace.getByRole('button', { name: 'Adopt this mark' }).click();
  }
  await expect(adopted.first()).toBeVisible();
}

/**
 * Certify as the preparer: place, adopt, name the approver, prove presence, sign.
 *
 * One press at the end, because the backend treats it as one transaction — the
 * placement, the signature and the routing commit together. On success the return
 * is `preparer_certified` and sitting in the named approver's queue.
 */
export async function certifyAsPreparer(
  page: Page,
  code: string,
  date: string,
  approverLabel = 'E2E Approver (approver)',
): Promise<void> {
  await page.goto(returnsUrl(code, date));
  const certify = page.getByRole('button', { name: 'Certify and freeze' });
  await expect(certify).toBeEnabled({ timeout: 30_000 });
  await certify.click();

  const workspace = page.getByTestId('signing-workspace');
  await expect(workspace).toBeVisible({ timeout: 30_000 });
  await placeBothSignatureFields(page, workspace);
  await adoptTypedMark(workspace);
  await workspace.getByLabel('Approver recipient').selectOption({ label: approverLabel });

  await workspace.getByLabel('Your password').fill(E2E_PASSWORD);
  await workspace.getByRole('button', { name: 'Certify and send' }).click();
  // The workspace closes only on success; a refusal keeps it open with the reason.
  await expect(workspace).toBeHidden({ timeout: CEREMONY_TIMEOUT });
  await expect(page.getByText('Preparer certified').first()).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * Approve and sign as the checker, from the queue — the founder's one act.
 *
 * Entered through the Approvals tab on purpose: that page is the queue and its
 * only action routes into the ceremony, so this also proves there is no bare
 * approve button left to bypass signing with. One press records the signature AND
 * the approval decision, which is why the return reaches `approved` without any
 * second visit anywhere.
 */
export async function approveAndSignAsChecker(
  browser: Browser,
  storageState: string,
  date: string,
): Promise<void> {
  const context = await browser.newContext({ storageState });
  try {
    const page = await context.newPage();
    await page.goto('/submissions/approvals');
    const row = page.getByRole('row', { name: new RegExp(fmtDateGB(date)) });
    await expect(row.first()).toBeVisible({ timeout: 30_000 });
    await row.first().click();

    await page.getByTestId('review-and-sign').click();
    const workspace = page.getByTestId('signing-workspace');
    await expect(workspace).toBeVisible({ timeout: RENDER_TIMEOUT });
    // The fields are part of the certified revision now, so there is nothing to
    // place — the palette is gone and the boxes cannot be moved.
    await expect(workspace.getByTestId('field-palette')).toHaveCount(0);
    // The approver must be shown what the preparer committed to, verified.
    await expect(
      workspace.getByText(/identical figures the preparer certified/i),
    ).toBeVisible({ timeout: 30_000 });
    await adoptTypedMark(workspace);

    await workspace.getByLabel('Your password').fill(E2E_PASSWORD);
    await workspace.getByRole('button', { name: 'Approve and sign' }).click();
    await expect(workspace).toBeHidden({ timeout: CEREMONY_TIMEOUT });
    // Signing IS approving: the checker queue clears without a second decision.
    await page.goto('/submissions/approvals');
    await expect(page.getByText('Queue is clear')).toBeVisible({ timeout: 30_000 });
  } finally {
    await context.close();
  }
}

/**
 * The reviewer's other exit: send it back with a note, from the same screen.
 *
 * Nothing is signed and no step-up is needed — the reviewer is refusing, not
 * committing. Entered through the queue for the same reason as the approval: the
 * decision is taken on the document, not on a row.
 */
export async function sendBackAsChecker(
  browser: Browser,
  storageState: string,
  date: string,
  note: string,
): Promise<void> {
  const context = await browser.newContext({ storageState });
  try {
    const page = await context.newPage();
    await page.goto('/submissions/approvals');
    const row = page.getByRole('row', { name: new RegExp(fmtDateGB(date)) });
    await expect(row.first()).toBeVisible({ timeout: 30_000 });
    await row.first().click();
    await page.getByTestId('review-and-sign').click();

    const workspace = page.getByTestId('signing-workspace');
    await expect(workspace).toBeVisible({ timeout: RENDER_TIMEOUT });
    const panel = workspace.getByTestId('send-back-panel');
    const send = panel.getByRole('button', { name: 'Send back for corrections' });
    // The note IS the instruction, so the exit is closed until there is one.
    await expect(send).toBeDisabled();
    await panel.getByRole('textbox').fill(note);
    await expect(send).toBeEnabled();
    await send.click();
    await expect(workspace).toBeHidden({ timeout: CEREMONY_TIMEOUT });

    // Off the checker queue: it is the preparer's again.
    await page.goto('/submissions/approvals');
    await expect(page.getByText('Queue is clear')).toBeVisible({ timeout: 30_000 });
  } finally {
    await context.close();
  }
}
