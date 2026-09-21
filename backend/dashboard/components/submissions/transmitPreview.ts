/**
 * What the transmit confirmation says about each file — as pure data.
 *
 * Split from the dialog because the dashboard's test harness compiles to plain
 * CommonJS and does not resolve the `@/` alias, so anything a test needs to
 * reach must not import the app's aliased modules. The same split as
 * `lib/api/icaapFilingNormalize.ts`.
 *
 * The rules encoded here are filing rules, not presentation preferences:
 *
 *  - The formula copy is FILED and NEVER SIGNED (founder decision 2026-09-20).
 *    It recalculates when opened, so it cannot be the record of truth; the
 *    values-only artifact and the PDF are. Any surface that shows it must say
 *    so — this is where that sentence is kept.
 *  - A file that does not exist yet is exported AT submission. Saying "—" for
 *    its size would read as "nothing to send"; it is the opposite.
 */

/** What one file is FOR in the filing — not what format it happens to be. */
export type FilingRole =
  | 'signed_record'
  | 'official_copy'
  | 'formula_copy'
  | 'data';

export type Tone = 'ok' | 'warn' | 'plain';

export type FilingSetEntry = Readonly<{
  kind: string;
  filename: string;
  role: FilingRole;
  /** Null when the file does not exist yet and is exported at submission. */
  sizeBytes: number | null;
  generatedAtSubmission: boolean;
  /** Signatures carried by this file; null where signing does not apply. */
  signatureCount: number | null;
}>;

export type TransmitPreview = Readonly<{
  returnCode: string;
  returnName: string;
  reportingDate: string;
  institutionName: string;
  deadline: string | null;
  channelLabel: string;
  isSimulated: boolean;
  /** The regulator's own code for this reporting institution, when held. */
  institutionCode: string | null;
  submissionRevision: string;
  isFirstFiling: boolean;
  filingSet: readonly FilingSetEntry[];
  /** Gates the server has already satisfied, phrased as settled facts. */
  satisfied: readonly string[];
  contentDigest: string | null;
  /**
   * Why the set is not larger, when the reason is a RULE rather than work not
   * yet done. A one-file filing can be entirely correct — this return may
   * produce no live-formula workbook and carry no signature — and an officer
   * cannot tell that by counting files.
   */
  omissions: readonly string[];
}>;

export const ROLE_COPY: Record<FilingRole, { label: string; tone: Tone }> = {
  signed_record: { label: 'The signed record of truth', tone: 'ok' },
  official_copy: { label: 'Official layout, values only', tone: 'plain' },
  formula_copy: {
    label: 'Formula copy — recalculates when opened',
    tone: 'warn',
  },
  data: { label: 'Machine-readable sections', tone: 'plain' },
};

export const TONE_CLASS: Record<Tone, string> = {
  ok: 'text-positive',
  warn: 'text-warning',
  plain: 'text-slate',
};

/**
 * The "Signed" cell.
 *
 * The formula copy answers before the signature count is even consulted: it is
 * never signed by construction, and a blank or a "0" there would invite the
 * reader to treat it as an oversight to be corrected.
 */
export function signedCell(entry: FilingSetEntry): { text: string; tone: Tone } {
  if (entry.role === 'formula_copy') return { text: 'Never signed', tone: 'warn' };
  if (entry.signatureCount === null) return { text: '—', tone: 'plain' };
  if (entry.signatureCount === 0) return { text: 'Unsigned', tone: 'plain' };
  const plural = entry.signatureCount === 1 ? 'signature' : 'signatures';
  return { text: `${entry.signatureCount} ${plural}`, tone: 'ok' };
}

/** The size cell: a file exported at submission is not a missing file. */
export function sizeCell(
  entry: FilingSetEntry,
  formatBytes: (bytes: number) => string,
): string {
  if (entry.generatedAtSubmission) return 'Generated now';
  if (entry.sizeBytes === null) return '—';
  return formatBytes(entry.sizeBytes);
}

/** "4 files" / "1 file" — the count is part of the officer's check. */
export function filingSetCount(preview: TransmitPreview): string {
  const n = preview.filingSet.length;
  return `${n} ${n === 1 ? 'file' : 'files'}`;
}
