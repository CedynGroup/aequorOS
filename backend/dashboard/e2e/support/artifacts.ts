/**
 * Reading the files a return's artifact buttons hand over.
 *
 * A journey that stops at "the download started" proves only that a button
 * exists. These readers open what the browser actually saved, so a journey can
 * assert on the return inside it: the official workbook's sheets, whether its
 * cells are values or live formulas, whether its sheets are locked, what the
 * signed PDF carries, and what the machine-readable bundle holds.
 *
 * Deliberately dependency-free for the archives: an XLSX workbook and the CSV
 * bundle are both plain zip files, and the handful of facts a journey checks
 * are attributes on a few XML parts. The PDF is opened with pdf.js, which the
 * dashboard already ships to render the document an officer signs.
 */

import path from "node:path";
import { inflateRawSync } from "node:zlib";

const END_OF_CENTRAL_DIRECTORY = 0x06054b50;
const CENTRAL_DIRECTORY_ENTRY = 0x02014b50;
const STORED = 0;
const DEFLATED = 8;

/**
 * Every entry of a zip archive, by name.
 *
 * Reads the central directory rather than walking local headers, because a
 * writer may defer sizes to a data descriptor that only the directory states.
 * No ZIP64: a return's artifacts are far below its 4 GiB threshold.
 */
export function readZip(bytes: Buffer): Map<string, Buffer> {
  let end = bytes.length - 22;
  while (end >= 0 && bytes.readUInt32LE(end) !== END_OF_CENTRAL_DIRECTORY) {
    end -= 1;
  }
  if (end < 0)
    throw new Error("Not a zip archive: no end of central directory.");

  const entries = new Map<string, Buffer>();
  const count = bytes.readUInt16LE(end + 10);
  let cursor = bytes.readUInt32LE(end + 16);
  for (let index = 0; index < count; index += 1) {
    if (bytes.readUInt32LE(cursor) !== CENTRAL_DIRECTORY_ENTRY) {
      throw new Error(`Corrupt zip central directory at byte ${cursor}.`);
    }
    const method = bytes.readUInt16LE(cursor + 10);
    const compressedSize = bytes.readUInt32LE(cursor + 20);
    const nameLength = bytes.readUInt16LE(cursor + 28);
    const extraLength = bytes.readUInt16LE(cursor + 30);
    const commentLength = bytes.readUInt16LE(cursor + 32);
    const localHeader = bytes.readUInt32LE(cursor + 42);
    const name = bytes.toString("utf8", cursor + 46, cursor + 46 + nameLength);

    const dataStart =
      localHeader +
      30 +
      bytes.readUInt16LE(localHeader + 26) +
      bytes.readUInt16LE(localHeader + 28);
    const data = bytes.subarray(dataStart, dataStart + compressedSize);
    if (method === STORED) entries.set(name, Buffer.from(data));
    else if (method === DEFLATED) entries.set(name, inflateRawSync(data));
    else
      throw new Error(`Zip entry ${name} uses unsupported method ${method}.`);

    cursor += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

const XML_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
};

function decodeXml(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, entity: string) => {
    if (entity.startsWith("#x") || entity.startsWith("#X")) {
      return String.fromCodePoint(parseInt(entity.slice(2), 16));
    }
    if (entity.startsWith("#")) {
      return String.fromCodePoint(parseInt(entity.slice(1), 10));
    }
    return XML_ENTITIES[entity] ?? whole;
  });
}

function attributes(tag: string): Record<string, string> {
  const found: Record<string, string> = {};
  for (const match of tag.matchAll(/([\w:]+)="([^"]*)"/g)) {
    found[match[1]] = decodeXml(match[2]);
  }
  return found;
}

/** The concatenated text runs (`<t>`) inside one XML fragment. */
function textRuns(fragment: string): string {
  return Array.from(fragment.matchAll(/<t(?:\s[^>]*)?>([^<]*)<\/t>/g))
    .map((match) => decodeXml(match[1]))
    .join("");
}

export type WorkbookSheet = Readonly<{
  name: string;
  /** Locked against edits — the sealed official copy's guarantee. */
  isProtected: boolean;
  /** Cells that carry a live formula rather than a stored value. */
  formulaCount: number;
  /** A cell's stored value: a number, a string, or null when blank. */
  cell: (ref: string) => number | string | null;
  /** Every string the sheet's cells store, for asserting on its own labelling. */
  strings: readonly string[];
}>;

export type Workbook = Readonly<{
  /** The workbook's document title (docProps/core.xml). */
  title: string | null;
  /** Sheets in workbook order. */
  sheets: readonly WorkbookSheet[];
  sheet: (name: string) => WorkbookSheet;
}>;

function readSheet(
  name: string,
  xml: string,
  sharedStrings: readonly string[],
): WorkbookSheet {
  const cells = new Map<string, number | string | null>();
  for (const match of xml.matchAll(/<c\s([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
    const attrs = attributes(match[1]);
    const body = match[2] ?? "";
    const stored = /<v>([^<]*)<\/v>/.exec(body)?.[1];
    let value: number | string | null = null;
    if (attrs.t === "s" && stored !== undefined)
      value = sharedStrings[Number(stored)];
    else if (attrs.t === "inlineStr") value = textRuns(body);
    else if (attrs.t === "str" && stored !== undefined)
      value = decodeXml(stored);
    else if (stored) value = Number(stored);
    cells.set(attrs.r, value);
  }
  const protection = /<sheetProtection\b([^>]*)\/?>/.exec(xml);
  const locked = protection ? attributes(protection[1]).sheet : undefined;
  return {
    name,
    isProtected: locked === "1" || locked === "true",
    formulaCount: (xml.match(/<f[\s>/]/g) ?? []).length,
    cell: (ref) => cells.get(ref) ?? null,
    strings: Array.from(cells.values()).filter(
      (value): value is string => typeof value === "string",
    ),
  };
}

/** Open an XLSX workbook from its bytes. */
export function readWorkbook(bytes: Buffer): Workbook {
  const parts = readZip(bytes);
  const part = (name: string): string => {
    const found = parts.get(name);
    if (!found) throw new Error(`Workbook has no ${name} part.`);
    return found.toString("utf8");
  };

  const shared = parts.get("xl/sharedStrings.xml")?.toString("utf8") ?? "";
  const sharedStrings = Array.from(
    shared.matchAll(/<si>([\s\S]*?)<\/si>/g),
  ).map((match) => textRuns(match[1]));

  const targets = new Map<string, string>();
  for (const match of part("xl/_rels/workbook.xml.rels").matchAll(
    /<Relationship\s([^>]*?)\/?>/g,
  )) {
    const { Id, Target } = attributes(match[1]);
    // Relationship targets are relative to xl/ unless written absolute.
    targets.set(Id, Target.startsWith("/") ? Target.slice(1) : `xl/${Target}`);
  }

  const sheets = Array.from(
    part("xl/workbook.xml").matchAll(/<sheet\s([^>]*?)\/?>/g),
  ).map((match) => {
    const attrs = attributes(match[1]);
    return readSheet(
      attrs.name,
      part(targets.get(attrs["r:id"]) ?? ""),
      sharedStrings,
    );
  });

  const core = parts.get("docProps/core.xml")?.toString("utf8") ?? "";
  const title = /<dc:title(?:\s[^>]*)?>([^<]*)<\/dc:title>/.exec(core)?.[1];

  return {
    title: title === undefined ? null : decodeXml(title),
    sheets,
    sheet: (name) => {
      const found = sheets.find((sheet) => sheet.name === name);
      if (!found) throw new Error(`Workbook has no sheet named ${name}.`);
      return found;
    },
  };
}

export type PdfDocument = Readonly<{
  pageCount: number;
  /** The text of every page, in order. */
  text: string;
  /** Names of the document's signature fields, on any page. */
  signatureFields: readonly string[];
  /**
   * Signatures actually applied. pdf.js never exposes a signature field's
   * value, so this counts the signature dictionaries' `/ByteRange` entries,
   * which a signer must write uncompressed to be patchable.
   */
  signatureCount: number;
}>;

/** pdf.js's own font metrics, so text extraction needs no system fonts. */
const STANDARD_FONTS = `${path.join(
  __dirname,
  "..",
  "..",
  "node_modules",
  "pdfjs-dist",
  "standard_fonts",
)}${path.sep}`;

/** Open a PDF with pdf.js — the same engine the signing workspace renders with. */
export async function readPdf(bytes: Buffer): Promise<PdfDocument> {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const document = await pdfjs.getDocument({
    data: new Uint8Array(bytes),
    standardFontDataUrl: STANDARD_FONTS,
    isEvalSupported: false,
  }).promise;
  try {
    const pages: string[] = [];
    const signatureFields: string[] = [];
    for (let number = 1; number <= document.numPages; number += 1) {
      const page = await document.getPage(number);
      const content = await page.getTextContent();
      pages.push(
        content.items.map((item) => ("str" in item ? item.str : "")).join(" "),
      );
      for (const annotation of await page.getAnnotations()) {
        if (annotation.fieldType === "Sig") {
          signatureFields.push(annotation.fieldName);
        }
      }
    }
    return {
      pageCount: document.numPages,
      text: pages.join("\n"),
      signatureFields,
      signatureCount: bytes.toString("latin1").split("/ByteRange").length - 1,
    };
  } finally {
    await document.destroy();
  }
}
