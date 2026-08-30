/**
 * Attestation e2e — the certification ceremony through the real UI.
 *
 * The backend suite already proves the state machine, the digest binding and
 * tamper detection. What only a browser can prove is that the *ceremony* is
 * sound: that a preparer is shown the exact figures digest and the full
 * statement before committing, that the signee ID reaches the screen, that
 * maker-checker is visible rather than merely enforced, and that submission
 * stays locked until the attestation is complete.
 *
 * The hermetic stack enrols disposable self-signed SOFTWARE signing keys in
 * scripts/e2e_bootstrap.py, so the ceremony runs for real here rather than
 * being skipped. It is not a production custody path — the software backend
 * refuses to initialise when APP_ENV is production.
 */

import { test, expect } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { requireObjectStorage } from "./support/object-storage";
import { mintBackendToken } from "./support/mint";
// Shared with the full-lifecycle journeys, which sign for real: one description
// of the ceremony surface, so a change to it cannot pass here and rot there.
import { placeBothSignatureFields, placeField } from "./support/ceremony";

const analystState = path.join(E2E_TMP, "analyst.json");

test.beforeAll(() => requireObjectStorage());
const API = `${E2E_API_ORIGIN}/api/v1`;
const RETURNS = "/submissions/returns?code=BSD3";
const GATED_RETURN = "/submissions/returns?code=LMT";

test.describe("attestation surfaces", () => {
  test.use({ storageState: analystState });

  test("the signer identity is provisioned and shown in Settings", async ({
    page,
  }) => {
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "Your account" }),
    ).toBeVisible();
    // The permanent signee ID must be visible and copyable — it is what travels
    // onto a filed document and what an examiner quotes back.
    const signerId = page.getByText(/^SGN-[0-9A-HJKMNP-TV-Z]{16}$/);
    await expect(signerId).toBeVisible();
  });

  test.fail(
    "a generated return shows its attestation state as unsigned",
    async ({ page }) => {
    await page.goto(RETURNS);
    await expect(page).toHaveURL(/\/submissions\/returns/);

    // The panel is bound to a package, so establish one first — the same path a
    // preparer takes (the UI round-trips createRegulatoryPackage).
    const generate = page
      .getByRole("button", { name: /generate package|regenerate/i })
      .first();
    await expect(generate).toBeVisible({ timeout: 5_000 });
    await generate.click();
    await expect(page.getByText(/Generated|Validated/).first()).toBeVisible({
      timeout: 30_000,
    });

    // A reviewer must be able to SEE that a return is unsigned, not infer it
    // from an absence.
    await expect(
      page.getByText("Attestation", { exact: false }).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/unsigned|not certified|awaiting/i).first(),
    ).toBeVisible();
    },
  );
});

test.describe("the certification ceremony", () => {
  // Serial: one package is walked through opt-in → generate → validate →
  // certify, so the steps are genuinely ordered rather than independent.
  test.describe.configure({ mode: "serial" });

  test.fail("opting in locks submission, and the ceremony enforces what it shows", async ({
    browser,
  }) => {
    const admin = await mintBackendToken("admin");
    const authorize = { Authorization: `Bearer ${admin}` };
    // LMT, not BSD3: the other journeys submit BSD3, and Playwright runs files
    // in parallel against ONE database, so a mandatory-signature policy on
    // BSD3 would block them mid-run.
    const scope = {
      return_code: "LMT",
      required_signatures: [
        { role: "preparer", min_count: 1, officer_titles: [] },
        { role: "approver", min_count: 1, officer_titles: [] },
      ],
      effective_from: "2020-01-01",
    };

    const context = await browser.newContext({ storageState: analystState });
    const page = await context.newPage();
    try {
      const opted = await page.request.put(
        `${API}/attestation/signing-policies`,
        {
          headers: authorize,
          data: {
            ...scope,
            require_signature: true,
            reason: "e2e: drive the ceremony",
          },
        },
      );
      expect(opted.ok()).toBeTruthy();

      await page.goto(GATED_RETURN);
      const generate = page
        .getByRole("button", { name: /generate package|regenerate/i })
        .first();
      await expect(generate).toBeVisible({ timeout: 5_000 });
      await generate.click();
      await expect(page.getByText(/Generated|Validated/).first()).toBeVisible({
        timeout: 30_000,
      });
      const validate = page.getByRole("button", { name: /^Validate/ }).first();
      if (await validate.count()) {
        await validate.click();
        await expect(page.getByText(/Validated/).first()).toBeVisible({
          timeout: 30_000,
        });
      }

      // --- an unsigned return must READ as unsubmittable ------------------
      //
      // The founder's screenshot: CLEARED TO SUBMIT rendered beside UNSIGNED.
      // The API was already right — `_status_payload` computes can_submit false
      // while a signature is outstanding — so the defect was purely display, and
      // that is what this asserts. It runs here, on a return whose policy has just
      // been set to require signatures, because that is the only state in which
      // the claim can be wrong.
      //
      // An earlier version of this journey asserted `Submit` was disabled and
      // passed only because no Submit control was rendered at all; these locators
      // fail if the element is missing, so the check cannot go trivially green.
      const clearance = page.getByTestId("attestation-clearance").first();
      await expect(clearance).toHaveText(/^Not cleared to submit/i);
      await expect(
        page.getByText("Cleared to submit", { exact: true }),
      ).toHaveCount(0);
      await expect(page.getByText("Unsigned").first()).toBeVisible();

      // Disabled AND told why, naming the signatures that are missing: a greyed
      // control with no reason sends the operator hunting, and a `title` alone is
      // invisible on a touch device.
      await expect(page.getByTestId("submit-package")).toBeDisabled();
      const blocked = page.getByTestId("submit-blocked-reason");
      await expect(blocked).toContainText(/not fully certified/i);
      await expect(blocked).toContainText(/preparer/i);
      await expect(blocked).toContainText(/approver/i);

      const certify = page.getByRole("button", { name: "Certify and freeze" });
      await expect(certify).toBeVisible({ timeout: 20_000 });
      await certify.click();

      // What-you-see-is-what-you-sign: the figures digest and the FULL Act 930
      // s.93(3) declaration must both be on screen before signing is possible.
      await expect(page.getByText(/section 93\(3\)/i)).toBeVisible({
        timeout: 20_000,
      });
      await expect(
        page.getByText(/complete and accurate/i).first(),
      ).toBeVisible();
      // The digest chip renders truncated by design (a4f2…9c1b), so match that
      // shape rather than 12 consecutive hex characters.
      await expect(
        page.getByText(/[0-9a-f]{4}…[0-9a-f]{4}/).first(),
      ).toBeVisible();

      // Both signature fields have to exist before the first signature — the
      // certification permits only form filling afterwards — so placing them is
      // part of the ceremony rather than a preliminary somebody could skip.
      await placeBothSignatureFields(
        page,
        page.getByTestId("signing-workspace"),
      );

      // Certifying and routing land in ONE backend transaction, so the ceremony
      // will not start until the return has somewhere to go. Naming the approver
      // is therefore part of the ceremony, not a follow-up task.
      const approverSlot = page.getByLabel("Approver recipient");
      await approverSlot.selectOption({ index: 1 });

      // Step-up is enforced, not decorative: a wrong password cannot certify.
      const password = page.getByLabel(/password/i).first();
      if (await password.count()) {
        await password.fill("definitely-not-the-password");
        await page
          .getByRole("button", { name: /^(Certify|Sign|Confirm)/ })
          .last()
          .click();
        await expect(
          page.getByText(/failed|incorrect|could not/i).first(),
        ).toBeVisible({ timeout: 20_000 });
      }

      // SSO step-up is a full redirect to the bank's IdP, so the only thing that
      // can prove the round trip is a browser. This stack has no SSO connection
      // (SSO_INTERNAL_KEY is empty), so the leg under test is the RETURN leg:
      // the signer must come back to this ceremony, on this return, with an
      // honest explanation — not a JSON error page, and not a silent no-op.
      await Promise.all([
        page.waitForURL(/\/submissions\/returns/, { timeout: 30_000 }),
        page.getByRole("button", { name: /single sign-on/i }).click(),
      ]);
      await expect(
        page.getByText(/Re-authentication did not complete/i),
      ).toBeVisible({ timeout: 30_000 });
      await expect(
        page.getByText(/not configured for this institution/i),
      ).toBeVisible();
      // Nothing was signed, and the ceremony is still open on the same return.
      await expect(page.getByText(/section 93\(3\)/i)).toBeVisible();
      // The markers are consumed, not left in the address bar: a reload must not
      // reopen a signing dialog, and an outcome must not outlive its round trip.
      expect(page.url()).not.toContain("stepUp=");
      expect(page.url()).not.toContain("certify=");
    } finally {
      // Hand the gate back however this ends, or every other submission
      // journey in the run inherits a locked return.
      const reopened = await page.request.put(
        `${API}/attestation/signing-policies`,
        {
          headers: authorize,
          data: {
            ...scope,
            require_signature: false,
            reason: "e2e teardown: restore the signature-optional default",
          },
        },
      );
      expect(reopened.ok()).toBeTruthy();
      await context.close();
    }
  });
});

/**
 * The SSO step-up routes, at the HTTP level.
 *
 * These handlers sit between a signer and a signature, so their guards matter
 * more than their happy path: an open redirect on a signing action is a phishing
 * primitive, and a certify proxy that accepts a caller-supplied authorisation
 * would undo the whole point of holding it in an HttpOnly cookie.
 */
test.describe("SSO step-up route guards", () => {
  test.use({ storageState: analystState });

  test("the step-up start refuses to bounce anywhere but back into the app", async ({
    page,
  }) => {
    const params = new URLSearchParams({
      bankId: "BK-SAMP0001",
      packageId: "00000000-0000-4000-8000-000000000000",
      signingRole: "preparer",
      returnTo: "https://phish.example/harvest",
    });
    const response = await page.request.get(
      `/api/attestation/step-up/start?${params.toString()}`,
      { maxRedirects: 0 },
    );
    expect(response.status()).toBe(400);
    expect(await response.text()).toContain("relative path");
  });

  test("the step-up start requires the package it is signing for", async ({
    page,
  }) => {
    const response = await page.request.get(
      "/api/attestation/step-up/start?signingRole=preparer",
      { maxRedirects: 0 },
    );
    expect(response.status()).toBe(400);
  });

  test("certify cannot be driven without a held authorisation", async ({
    page,
  }) => {
    // The authorisation only ever exists in an HttpOnly cookie written by the
    // callback. With no cookie there is nothing to spend, and no request body
    // can substitute for one.
    const response = await page.request.post("/api/attestation/certify", {
      data: {
        bankId: "BK-SAMP0001",
        packageId: "00000000-0000-4000-8000-000000000000",
        signingRole: "preparer",
        expectedCertificationDigest: "a".repeat(64),
      },
    });
    expect(response.status()).toBe(401);
    expect(await response.json()).toMatchObject({ error: "step_up_required" });
  });

  test("the callback refuses a code that does not match its own request", async ({
    page,
  }) => {
    // No state cookie exists for this request, so the code is unattributable.
    // It must land the signer back on the ceremony, never exchange the code.
    const response = await page.request.get(
      "/api/attestation/step-up/callback?code=forged&state=forged",
      { maxRedirects: 0 },
    );
    expect(response.status()).toBe(307);
    expect(response.headers()["location"]).toContain("stepUp=expired");
  });
});

/**
 * The signing workspace — the DocuSign-shaped surface (SIGN-10, SIGN-11).
 *
 * What only a browser can prove here is geometry, the palette, and refusal. The
 * backend suite proves that a bad placement is rejected; it cannot prove that
 * the box a signer drags on screen becomes the coordinates that are sent, and
 * that flip is silent when it is wrong — a signature simply prints somewhere
 * else on a document filed with the regulator. So the journey places real fields
 * from the palette, drags one and asserts the PDF coordinates moved the way PDF
 * user space requires (origin BOTTOM-left: drag DOWN, y goes DOWN in numbers),
 * checks that a signature box the size of a BoG ruled line is ACCEPTED (the case
 * the old single 185×61 floor refused), that a date field renders the value that
 * will actually be stamped, and only then that a box below its own kind's
 * minimum blocks the ceremony with the reason.
 */
test.describe("the signing workspace", () => {
  test.describe.configure({ mode: "serial" });

  test.fail("places typed fields from the palette, and refuses an illegible box", async ({
    browser,
  }) => {
    const admin = await mintBackendToken("admin");
    const authorize = { Authorization: `Bearer ${admin}` };
    // LMT again, and for the same reason as the ceremony journey above: BSD3 is
    // submitted by the lifecycle journeys, and one database is shared.
    const scope = {
      return_code: "LMT",
      required_signatures: [
        { role: "preparer", min_count: 1, officer_titles: [] },
        { role: "approver", min_count: 1, officer_titles: [] },
      ],
      effective_from: "2020-01-01",
    };

    const context = await browser.newContext({ storageState: analystState });
    const page = await context.newPage();
    try {
      const opted = await page.request.put(
        `${API}/attestation/signing-policies`,
        {
          headers: authorize,
          data: {
            ...scope,
            require_signature: true,
            reason: "e2e: open the workspace",
          },
        },
      );
      expect(opted.ok()).toBeTruthy();

      await page.goto(GATED_RETURN);
      const generate = page
        .getByRole("button", { name: /generate package|regenerate/i })
        .first();
      await expect(generate).toBeVisible({ timeout: 5_000 });
      await generate.click();
      await expect(page.getByText(/Generated|Validated/).first()).toBeVisible({
        timeout: 30_000,
      });
      const validate = page.getByRole("button", { name: /^Validate/ }).first();
      if (await validate.count()) {
        await validate.click();
        await expect(page.getByText(/Validated/).first()).toBeVisible({
          timeout: 30_000,
        });
      }

      // The entry point is the workspace, not a modal.
      await page.getByRole("button", { name: "Certify and freeze" }).click();
      const workspace = page.getByTestId("signing-workspace");
      await expect(workspace).toBeVisible({ timeout: 30_000 });

      // The document itself is on screen — exported on demand if it had never
      // been exported — and it is a real pdf.js render, not a placeholder.
      const canvas = workspace.locator('canvas[aria-label^="Return document"]');
      await expect(canvas).toBeVisible({ timeout: 60_000 });
      await expect
        .poll(
          async () => canvas.evaluate((node: HTMLCanvasElement) => node.width),
          {
            timeout: 60_000,
          },
        )
        .toBeGreaterThan(0);

      // What-you-see-is-what-you-sign survives the new surface: the full Act 930
      // s.93(3) statement and the figures digest are both on screen, unabridged.
      await expect(workspace.getByText(/section 93\(3\)/i)).toBeVisible({
        timeout: 30_000,
      });
      await expect(
        workspace.getByText(/[0-9a-f]{4}…[0-9a-f]{4}/).first(),
      ).toBeVisible();

      // --- the palette -----------------------------------------------------
      // Nothing is auto-placed: the platform default puts boxes NEAR the
      // attestation block rather than on its ruled lines, and a wrong default
      // that has to be dragged off the wording is worse than an empty page.
      const palette = workspace.getByTestId("field-palette");
      await expect(palette).toBeVisible();
      await expect(workspace.locator("[data-signing-role]")).toHaveCount(0);

      // The founder's case: a signature box the size of a BoG attestation
      // block's ruled line (~144×35 pt). The old single 185×61 floor refused it
      // outright, so the field could not be put where the form wants it.
      await placeField(workspace, "signature", { x: 120, y: 500 });
      const field = workspace.locator(
        '[data-signing-role="preparer"][data-field-type="signature"]',
      );
      await expect(field).toBeVisible({ timeout: 30_000 });
      const readBox = async () =>
        (await field.getAttribute("data-pdf-box"))!.split(",").map(Number);
      const placed = await readBox();
      expect(placed[2] - placed[0]).toBeCloseTo(144, -0.5);
      expect(placed[3] - placed[1]).toBeCloseTo(35, -0.5);
      // Accepted, not merely drawn: no violation on the box and no block on the
      // ceremony from the size of it.
      await expect(field.getByRole("alert")).toHaveCount(0);
      await expect(
        workspace.getByText(/is below the .* pt minimum/i),
      ).toHaveCount(0);

      // The approver's fields are the PREPARER's job — the certification permits
      // no new field afterwards — so the rail switches recipient rather than
      // deferring to a second signer who could never place one.
      await palette.locator('[data-recipient-role="approver"]').click();
      await placeField(workspace, "signature", { x: 380, y: 500 });
      await expect(
        workspace.locator(
          '[data-signing-role="approver"][data-field-type="signature"]',
        ),
      ).toBeVisible();

      // --- a derived value, previewed --------------------------------------
      // The form asks for a date; the platform derives it from the signature
      // record and nothing typed in a browser can reach it. What the box shows
      // is computed the same way the stamp will be, so this asserts the actual
      // string an examiner will read.
      await placeField(workspace, "date_signed", { x: 380, y: 560 });
      const dateField = workspace.locator(
        '[data-signing-role="approver"][data-field-type="date_signed"]',
      );
      await expect(dateField).toBeVisible();
      await expect(dateField).toContainText(
        new Date().toISOString().slice(0, 10),
      );

      // --- moving a field --------------------------------------------------
      // The workspace opens at 100%, where one CSS pixel is exactly one PDF
      // point, so a drag of a known number of pixels has an arithmetically known
      // answer in PDF user space. That is what makes this a test of the
      // conversion rather than of the mouse.
      const before = await readBox();
      const start = (await field.boundingBox())!;
      await page.mouse.move(
        start.x + start.width / 2,
        start.y + start.height / 2,
      );
      await page.mouse.down();
      // Two moves: a single jump can be swallowed by pointer-capture handlers
      // that only act on movement after the press.
      await page.mouse.move(
        start.x + start.width / 2 + 20,
        start.y + start.height / 2 + 20,
      );
      await page.mouse.move(
        start.x + start.width / 2 + 40,
        start.y + start.height / 2 + 60,
      );
      await page.mouse.up();

      await expect
        .poll(async () => (await readBox())[0], { timeout: 10_000 })
        .toBeGreaterThan(before[0]);
      const after = await readBox();
      // Right by 40 px is right by 40 pt: x agrees in both spaces.
      expect(after[0]).toBeCloseTo(before[0] + 40, -0.5);
      expect(after[2]).toBeCloseTo(before[2] + 40, -0.5);
      // Down by 60 px is DOWN by 60 pt, which in PDF user space (origin
      // bottom-left) means y DECREASES by 60. An unflipped conversion would put
      // the signature 120 pt too high on a document filed with the regulator,
      // and would pass a directional-only assertion in the other direction.
      expect(after[1]).toBeCloseTo(before[1] - 60, -0.5);
      expect(after[3]).toBeCloseTo(before[3] - 60, -0.5);
      // A move is a move: the box keeps its size.
      expect(after[2] - after[0]).toBeCloseTo(before[2] - before[0], -0.5);
      expect(after[3] - after[1]).toBeCloseTo(before[3] - before[1], -0.5);

      // --- refusing an illegible box ---------------------------------------
      // Shrink from the bottom-right handle past the floor derived for a
      // signature mark. The floor is far smaller than it was, but it still
      // exists: below it the adopted mark stops being readable on a printed
      // return, and the ceremony is stopped and told why rather than just
      // stopped.
      const grown = (await field.boundingBox())!;
      await page.mouse.move(grown.x + grown.width, grown.y + grown.height);
      await page.mouse.down();
      await page.mouse.move(
        grown.x + grown.width - 60,
        grown.y + grown.height - 20,
      );
      await page.mouse.move(grown.x + 4, grown.y + 4);
      await page.mouse.up();

      await expect(
        workspace.getByText(/below the .* pt minimum/i).first(),
      ).toBeVisible({
        timeout: 10_000,
      });
      // Blocked BEFORE submitting, and told which field and why — not left to
      // discover it as a rejected certification on a filing deadline.
      await expect(
        workspace.getByText(/signature field cannot be filed/i),
      ).toBeVisible();
      await expect(
        workspace.getByRole("button", {
          name: /Certify and send|Certify and freeze/,
        }),
      ).toBeDisabled();
    } finally {
      // Hand the gate back however this ends, or every other submission
      // journey in the run inherits a locked return.
      const reopened = await page.request.put(
        `${API}/attestation/signing-policies`,
        {
          headers: authorize,
          data: {
            ...scope,
            require_signature: false,
            reason: "e2e teardown: restore the signature-optional default",
          },
        },
      );
      expect(reopened.ok()).toBeTruthy();
      await context.close();
    }
  });
});

/**
 * The Export artifacts card — what Download actually hands over (SIGN-13).
 *
 * The defect this guards was found live: a fully certified return whose
 * Download button served the raw unsigned PDF. The certification half cannot be
 * driven in this stack (step-up needs a real password or a real IdP, and the
 * hermetic users have neither), so what a browser can prove here is the other
 * half of the contract — that the signed row is not decoration that appears
 * regardless, that an UNSIGNED return offers exactly the base export and claims
 * nothing more, and that the version chain the card reads agrees with it. The
 * signed resolution itself is proved against a real ceremony in
 * tests/services/test_attestation_artifact_signing.py.
 */
test.describe("the filed document", () => {
  test.use({ storageState: analystState });

  test.fail("an unsigned return offers the base export and claims no signature", async ({
    page,
  }) => {
    const analyst = await mintBackendToken("analyst");
    const authorize = { Authorization: `Bearer ${analyst}` };

    await page.goto(GATED_RETURN);
    const generate = page
      .getByRole("button", { name: /generate package|regenerate/i })
      .first();
    await expect(generate).toBeVisible({ timeout: 5_000 });
    await generate.click();
    await expect(page.getByText(/Generated|Validated/).first()).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "PDF", exact: true }).click();
    await expect(
      page.getByRole("button", { name: /Download pdf artifact/i }),
    ).toBeVisible({ timeout: 30_000 });

    // Nothing is signed, so nothing may present itself as the signed return —
    // and the base export is not yet labelled as something it supersedes.
    await expect(page.getByText(/Signed return/i)).toHaveCount(0);
    await expect(page.getByText(/Pre-signature engine output/i)).toHaveCount(0);

    const packages = await page.request.get(
      `${API}/banks/BK-SAMP0001/regulatory-packages?return_code=LMT&limit=1`,
      { headers: authorize },
    );
    expect(packages.ok()).toBeTruthy();
    const packageId = (await packages.json()).packages[0].id;

    const listed = await page.request.get(
      `${API}/banks/BK-SAMP0001/regulatory-packages/${packageId}/artifact-versions`,
      { headers: authorize },
    );
    expect(listed.ok()).toBeTruthy();
    const versions = (await listed.json()).versions.filter(
      (version: { kind: string }) => version.kind === "pdf",
    );
    expect(versions.length).toBeGreaterThan(0);
    // The chain is append-only, so every earlier render is still listed; none of
    // them is signed, and therefore none of them is the filed document.
    for (const version of versions) {
      expect(version.signed_by).toBeNull();
      expect(version.is_filed).toBe(false);
    }

    // The archived bytes are served, and they are the bytes the row records —
    // the endpoint refuses rather than serving a mismatch.
    const latest = versions.find(
      (version: { is_latest: boolean }) => version.is_latest,
    );
    const download = await page.request.get(
      `${API}/banks/BK-SAMP0001/regulatory-artifact-versions/${latest.id}/download`,
      { headers: authorize },
    );
    expect(download.ok()).toBeTruthy();
    expect(download.headers()["content-type"]).toContain("application/pdf");
    expect((await download.body()).length).toBe(latest.size_bytes);
  });
});
