/**
 * The digest a checker's decision on a calculated measure is taken against.
 *
 * A MODULE OF ITS OWN, AND THAT IS THE POINT. `lib/api/bi.ts` needs this one
 * function, and `lib/api/bi.ts` is in the Command Center's initial bundle (the
 * home insight strip reads `…/bi/insights` through it). While this lived in
 * `components/bi/measures.ts` the whole calculated-measure vocabulary — every
 * function signature, every state sentence, every withheld-control explanation —
 * rode into that bundle with it. Measured, not assumed: five strings unique to
 * that module were found in `static/chunks/…` of the `/` entry graph, and the
 * Command Center's initial JS was 10 570 B larger for it.
 *
 * So the rule for this file is: nothing but the digest. Anything that belongs to
 * the measure SURFACE belongs in `measures.ts`, which reaches only the routes that
 * draw it. `measures.ts` re-exports both names, so callers on that surface still
 * have one import site.
 */

/** `crypto.subtle` was not available, so no decision can honestly be sent. */
export class DigestUnavailable extends Error {
  constructor() {
    super(
      "This browser could not work out which version of the formula is on " +
        "screen, so the decision was not sent. Reload the page over a secure " +
        "connection and try again.",
    );
    this.name = "DigestUnavailable";
  }
}

/**
 * SHA-256 of a formula's source text, which is exactly what the server stores
 * (`content.expression_digest` — a digest of the SOURCE, not of the parsed tree,
 * because a checker approves the text a person can read).
 *
 * WHY THE CLIENT COMPUTES THIS AT ALL. `POST …/bi/measures/{id}/decision`
 * requires `expression_digest`: the version the checker read. The server refuses
 * the decision if the formula has moved since, which is the whole point — a
 * decision taken against a version that no longer exists is not a decision about
 * what would be certified. `BiMeasureRead` does not carry the digest, so the only
 * honest token available is one taken over the very text the checker was shown.
 * It is a stale-read check, not a second opinion about the language: nothing here
 * parses anything. `measures.test.ts` checks it against an independent SHA-256,
 * including a non-ASCII formula, because the server hashes UTF-8 bytes.
 *
 * A backend that published `expression_digest` on the read model would let this
 * go; see the track report.
 */
export async function expressionDigest(source: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new DigestUnavailable();
  const bytes = new TextEncoder().encode(source);
  const digest = await subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
