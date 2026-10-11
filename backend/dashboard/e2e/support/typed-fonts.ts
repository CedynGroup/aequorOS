import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";
import {
  expect,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";
import { E2E_API_ORIGIN } from "../../playwright.config";
import { mintBackendToken } from "./mint";

type Face = {
  key: string;
  label: string;
  postScriptName: string | null;
  sha256: string | null;
};

type Appearance = {
  adopted: boolean;
  kind: "typed" | "drawn" | null;
  typed_name: string | null;
  typed_font: string | null;
  image_png_base64: string | null;
  available_fonts: string[];
};

const standardStyles: Record<string, [string, string, string]> = {
  times_italic: ['"Times New Roman", Times, serif', "italic", "400"],
  times_bold_italic: ['"Times New Roman", Times, serif', "italic", "700"],
  helvetica_oblique: ["Helvetica, Arial, sans-serif", "italic", "400"],
  courier_oblique: ['"Courier New", Courier, monospace', "italic", "400"],
};

export async function expectTypedSignatureFonts(
  page: Page,
  workspace: Locator,
  fontResponses: Response[],
): Promise<void> {
  const catalogue = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        `import hashlib, json
from fontTools.ttLib import TTFont
from app.attestation.service.typed_fonts import TYPED_FACES, default_face_key
faces = []
for face in TYPED_FACES.values():
    postscript = digest = None
    if face.path is not None:
        with TTFont(face.path) as font:
            postscript = font["name"].getDebugName(6)
        digest = hashlib.sha256(face.path.read_bytes()).hexdigest()
    faces.append(dict(key=face.key, label=face.label, postScriptName=postscript, sha256=digest))
print(json.dumps(dict(faces=faces, defaultKey=default_face_key())))`,
      ],
      { cwd: path.join(__dirname, "..", "..", ".."), encoding: "utf8" },
    ),
  ) as { faces: Face[]; defaultKey: string };
  const appearanceUrl = `${E2E_API_ORIGIN}/api/v1/attestation/my-signature-appearance`;
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("analyst")}`,
  };
  const originalResponse = await page.request.get(appearanceUrl, { headers });
  expect(originalResponse.ok()).toBeTruthy();
  const original = (await originalResponse.json()) as Appearance;
  expect(original.available_fonts).toEqual(
    catalogue.faces.map((face) => face.key),
  );

  await workspace.getByRole("tab", { name: "Type it" }).click();
  const nameInput = workspace.getByLabel("Name as it should appear");
  const originalName = await nameInput.inputValue();
  const styles = workspace.getByRole("group", { name: "Style", exact: true });
  const offers = styles.getByRole("button");
  await expect(offers).toHaveCount(catalogue.faces.length);
  await expect(offers.locator("span:last-child")).toHaveText(
    catalogue.faces.map((face) => face.label),
  );
  const initialKey = original.typed_font ?? catalogue.defaultKey;
  await expect(
    offers.nth(catalogue.faces.findIndex((face) => face.key === initialKey)),
  ).toHaveAttribute("aria-pressed", "true");
  await nameInput.fill("Ama Mensah");

  const session = await page.context().newCDPSession(page);
  await session.send("DOM.enable");
  await session.send("CSS.enable");
  const assertRenderedFace = async (selector: string, face: Face) => {
    const preview = page.locator(selector);
    await expect(preview).toBeVisible();
    await expect(preview).toHaveText("Ama Mensah");
    await preview.evaluate(async (element) => {
      const style = getComputedStyle(element);
      await document.fonts.load(
        `${style.fontSize} ${style.fontFamily}`,
        element.textContent ?? "",
      );
      await document.fonts.ready;
    });
    const { root } = await session.send("DOM.getDocument");
    const { nodeId } = await session.send("DOM.querySelector", {
      nodeId: root.nodeId,
      selector,
    });
    expect(nodeId).toBeGreaterThan(0);
    const { fonts } = await session.send("CSS.getPlatformFontsForNode", {
      nodeId,
    });
    const rendered = fonts.filter((font) => font.glyphCount > 0);
    expect(rendered).toHaveLength(1);
    if (face.sha256 !== null) {
      expect(face.postScriptName).not.toBeNull();
      expect(rendered[0].isCustomFont).toBe(true);
      expect(rendered[0].postScriptName).toBe(face.postScriptName);
    } else {
      expect(rendered[0].isCustomFont).toBe(false);
      const actualStyle = await preview.evaluate((element) => {
        const style = getComputedStyle(element);
        return [style.fontFamily, style.fontStyle, style.fontWeight];
      });
      expect(actualStyle).toEqual(standardStyles[face.key]);
    }
  };

  try {
    for (const [index, face] of catalogue.faces.entries()) {
      await assertRenderedFace(
        `[data-testid="signing-workspace"] fieldset button:nth-child(${index + 1}) > span:first-child`,
        face,
      );
      await offers.nth(index).click();
      await expect(offers.nth(index)).toHaveAttribute("aria-pressed", "true");
      const adoptedResponse = page.waitForResponse(
        (response) =>
          response.request().method() === "PUT" &&
          new URL(response.url()).pathname.endsWith(
            "/attestation/my-signature-appearance",
          ),
      );
      await workspace.getByRole("button", { name: "Adopt this mark" }).click();
      const adopted = await adoptedResponse;
      expect(adopted.ok()).toBeTruthy();
      expect(await adopted.json()).toMatchObject({
        adopted: true,
        kind: "typed",
        typed_name: "Ama Mensah",
        typed_font: face.key,
      });
      await assertRenderedFace(
        '[data-testid="signing-workspace"] [data-signing-role="preparer"][data-field-type="signature"] > span:nth-of-type(2)',
        face,
      );
    }
    const loadedHashes = await Promise.all(
      fontResponses.map(async (response) => {
        expect(response.ok()).toBeTruthy();
        expect(new URL(response.url()).origin).toBe(new URL(page.url()).origin);
        return createHash("sha256")
          .update(await response.body())
          .digest("hex");
      }),
    );
    for (const face of catalogue.faces.filter((face) => face.sha256 !== null)) {
      expect(loadedHashes, face.label).toContain(face.sha256);
    }
  } finally {
    await session.detach();
    const restored = await page.request.put(appearanceUrl, {
      headers,
      data: {
        kind: original.kind ?? "typed",
        typed_name: original.typed_name ?? originalName,
        typed_font: original.typed_font ?? catalogue.defaultKey,
        image_png_base64: original.image_png_base64,
      },
    });
    expect(restored.ok()).toBeTruthy();
  }
}
