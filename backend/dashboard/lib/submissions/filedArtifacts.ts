/**
 * What actually went to the regulator, read back from the submission record.
 *
 * At submission the server writes `filed_artifacts` into the event detail: the
 * kind, stored path, checksum and size of every file it sent, and which one
 * carried the signatures. That is the bank's evidence of what it filed — the
 * answer to "what exactly did we send them on the 9th?" — and until now it was
 * written and never shown.
 *
 * The detail is an open `dict[str, Any]` on the wire, so nothing here may
 * assume a shape. Every field is checked, a row that cannot be understood is
 * DROPPED rather than rendered half-empty, and an unreadable detail yields an
 * empty list — the card then says the record is unavailable instead of drawing
 * an authoritative-looking table of blanks.
 *
 * Pure: no imports from the app, so the node test harness can reach it.
 */

/** What one file was FOR in the filing. Mirrors the backend's roles. */
export type FiledRole = 'signed_record' | 'official_copy' | 'formula_copy' | 'data';

export type FiledArtifactRow = Readonly<{
  kind: string;
  filename: string;
  role: FiledRole;
  checksum: string | null;
  sizeBytes: number | null;
  signed: boolean;
}>;

const ROLE_BY_KIND: Record<string, FiledRole> = {
  pdf: 'signed_record',
  xlsx: 'official_copy',
  xlsx_working: 'formula_copy',
  docx_working: 'formula_copy',
  csv: 'data',
};

export const FILED_ROLE_LABEL: Record<FiledRole, string> = {
  signed_record: 'Signed record of truth',
  official_copy: 'Official layout, values only',
  formula_copy: 'Formula copy — not the signed record',
  data: 'Machine-readable sections',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function filename(objectPath: string): string {
  const tail = objectPath.split('/').pop();
  return tail && tail.length > 0 ? tail : objectPath;
}

/**
 * The role a filed artifact played.
 *
 * `signed` from the record wins over the kind: it is the server's own statement
 * of which bytes the officers covered, and a return whose signed revision is
 * not a PDF must not be re-labelled by a lookup table.
 */
export function roleFor(kind: string, signed: boolean): FiledRole {
  if (signed) return 'signed_record';
  const known = ROLE_BY_KIND[kind];
  if (known === undefined) return 'data';
  // An unsigned PDF is not the record of truth — say what it is, not what a
  // PDF usually is.
  return known === 'signed_record' ? 'official_copy' : known;
}

/** Read `filed_artifacts` out of a submission event's detail. */
export function filedArtifacts(detail: unknown): FiledArtifactRow[] {
  if (!isRecord(detail)) return [];
  const raw = detail.filed_artifacts;
  if (!Array.isArray(raw)) return [];

  const rows: FiledArtifactRow[] = [];
  for (const candidate of raw) {
    if (!isRecord(candidate)) continue;
    const kind = asString(candidate.kind);
    const objectPath = asString(candidate.object_path);
    // Without both of these the row cannot be identified as a file at all.
    if (kind === null || objectPath === null) continue;
    const signed = candidate.signed === true;
    rows.push({
      kind,
      filename: filename(objectPath),
      role: roleFor(kind, signed),
      checksum: asString(candidate.checksum_sha256),
      sizeBytes: asNumber(candidate.size_bytes),
      signed,
    });
  }
  return rows;
}

/** A checksum short enough to compare by eye, or null when absent. */
export function shortChecksum(checksum: string | null): string | null {
  if (checksum === null) return null;
  return checksum.length > 12
    ? `${checksum.slice(0, 4)}…${checksum.slice(-4)}`
    : checksum;
}
