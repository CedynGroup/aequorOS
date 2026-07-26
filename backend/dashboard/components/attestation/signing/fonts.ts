import localFont from 'next/font/local';
import type { CSSProperties } from 'react';
import type { AdoptSignatureRequestTypedFont } from '@aequoros/risk-service-api';

/**
 * The four bundled script faces, loaded from the *same* `.ttf` files the backend
 * embeds into the signed PDF (`app/services/attestation/fonts/`, SIL Open Font
 * License — see that directory's README).
 *
 * One copy, two consumers, on purpose. A second copy under `public/` would drift,
 * and the drift would be invisible: the signer would adopt the mark the browser
 * drew and file the mark the server drew. `next/font/local` self-hosts them out
 * of `_next/static`, so nothing here reaches Google Fonts — a dashboard that
 * fetches a webfont at render time shows a different signature (or none) to a
 * bank whose network blocks it.
 */
const caveat = localFont({
  src: '../../../../app/services/attestation/fonts/Caveat-Regular.ttf',
  display: 'swap',
});
const dancingScript = localFont({
  src: '../../../../app/services/attestation/fonts/DancingScript-Regular.ttf',
  display: 'swap',
});
const greatVibes = localFont({
  src: '../../../../app/services/attestation/fonts/GreatVibes-Regular.ttf',
  display: 'swap',
});
const allura = localFont({
  src: '../../../../app/services/attestation/fonts/Allura-Regular.ttf',
  display: 'swap',
});

/** Every adoptable mark, named as what it is. */
export const TYPED_FONT_LABELS: Record<string, string> = {
  caveat: 'Caveat',
  dancing_script: 'Dancing Script',
  great_vibes: 'Great Vibes',
  allura: 'Allura',
  times_italic: 'Times Italic (typeset)',
  times_bold_italic: 'Times Bold Italic (typeset)',
  helvetica_oblique: 'Helvetica Oblique (typeset)',
  courier_oblique: 'Courier Oblique (typeset)',
};

/**
 * How each key previews. The script keys resolve to the embedded file itself, so
 * the preview is the stamp; the base-14 keys resolve to the closest thing a
 * browser has, because the PDF names those faces rather than carrying them and
 * the reader supplies the glyphs either way.
 */
export const TYPED_FONT_STYLES: Record<string, CSSProperties> = {
  caveat: { fontFamily: caveat.style.fontFamily },
  dancing_script: { fontFamily: dancingScript.style.fontFamily },
  great_vibes: { fontFamily: greatVibes.style.fontFamily },
  allura: { fontFamily: allura.style.fontFamily },
  times_italic: { fontFamily: '"Times New Roman", Times, serif', fontStyle: 'italic' },
  times_bold_italic: {
    fontFamily: '"Times New Roman", Times, serif',
    fontStyle: 'italic',
    fontWeight: 700,
  },
  helvetica_oblique: { fontFamily: 'Helvetica, Arial, sans-serif', fontStyle: 'italic' },
  courier_oblique: { fontFamily: '"Courier New", Courier, monospace', fontStyle: 'italic' },
};

/** Matches `typed_fonts.PREFERRED_SCRIPT_KEY` — the panel's first offer. */
export const DEFAULT_TYPED_FONT: NonNullable<AdoptSignatureRequestTypedFont> = 'caveat';
