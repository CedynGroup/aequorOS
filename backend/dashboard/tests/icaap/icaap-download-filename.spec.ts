import fc from "fast-check";
import { expect, it } from "vitest";
import { filenameFrom } from "../../lib/api/icaap";

function responseWith(disposition: string): Response {
  return new Response(null, {
    headers: { "Content-Disposition": disposition },
  });
}

it.each([
  ["Board_minutes.pdf", "Board minutes.pdf"],
  ["Comit_minutes.pdf", "Comité minutes.pdf"],
  ["evidence.pdf", 'minutes"; filename="other.pdf'],
  ["evidence.pdf", "progress 100% + notes.pdf"],
])("prefers the original UTF-8 name over %s", (ascii, original) => {
  const extended = `filename*=UTF-8''${encodeURIComponent(original)}`;
  for (const disposition of [
    `attachment; filename="${ascii}"; ${extended}`,
    `attachment; ${extended}; filename="${ascii}"`,
    `attachment; ${extended}`,
  ]) {
    expect(filenameFrom(responseWith(disposition), "fallback.pdf")).toBe(
      original,
    );
  }
});

it.each([
  ['attachment; filename="Board minutes.pdf"', "Board minutes.pdf"],
  ["attachment; filename=draft.docx", "draft.docx"],
  [
    "attachment; filename=\"safe.pdf\"; filename*=UTF-8''broken%ZZ.pdf",
    "safe.pdf",
  ],
  [
    "attachment; filename=\"safe.pdf\"; filename*=UTF-8''invalid%C3.pdf",
    "safe.pdf",
  ],
  ["attachment; filename*=UTF-8''broken%", "fallback.pdf"],
  ["attachment", "fallback.pdf"],
])("retains the fallback for %s", (disposition, expected) => {
  expect(filenameFrom(responseWith(disposition), "fallback.pdf")).toBe(
    expected,
  );
});

it("uses the caller's fallback when the header is missing", () => {
  expect(filenameFrom(new Response(null), "draft.pdf")).toBe("draft.pdf");
});

it("round-trips encoded evidence filenames", () => {
  const filename = fc
    .array(
      fc.constantFrom("a", "Z", " ", "é", "中", "📄", "%", "+", ";", '"', "'"),
      { minLength: 1, maxLength: 60 },
    )
    .map((characters) => `${characters.join("")}.pdf`);
  fc.assert(
    fc.property(filename, (original) => {
      const response = responseWith(
        `attachment; filename="document.pdf"; filename*=UTF-8''${encodeURIComponent(original)}`,
      );
      expect(filenameFrom(response, "fallback.pdf")).toBe(original);
    }),
    { numRuns: 100 },
  );
});
