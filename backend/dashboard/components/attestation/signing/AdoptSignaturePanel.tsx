'use client';

/**
 * Adopting a mark — the visible half of a signature whose evidential half is a
 * digest (docs/attestation_esignature.md §2.5).
 *
 * The mark is NOT what makes the signature valid: the cryptographic signature
 * over the certification digest is, and the four evidential lines beside the
 * mark are what an examiner reads. The panel says so, because a signer who
 * believes their drawing is the security control will treat the ceremony
 * accordingly.
 *
 * Drawing uses pointer events so the same code path serves a trackpad, a mouse
 * and a pen — a trackpad emits pointer events with `pointerType: 'mouse'`, and
 * `setPointerCapture` is what stops a stroke breaking when the pointer leaves
 * the pad's travel. Strokes are captured at the exact pixel size the service
 * normalises to, so nothing is resampled twice.
 */

import { useEffect, useRef, useState } from 'react';
import { Check, Eraser, Loader2, PenTool, Type } from 'lucide-react';
import type {
  AdoptSignatureRequestTypedFont,
  AdoptedSignatureRead,
} from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useAdoptMySignature } from '@/lib/api/hooks';
import { DEFAULT_TYPED_FONT, TYPED_FONT_LABELS, TYPED_FONT_STYLES } from './fonts';

/** Matches `appearance.NORMALISED_WIDTH` × `NORMALISED_HEIGHT` exactly. */
const PAD_WIDTH = 600;
const PAD_HEIGHT = 200;

export default function AdoptSignaturePanel({
  adopted,
  signerName,
}: {
  adopted: AdoptedSignatureRead | undefined;
  /** The signer's own name — the default for a typed mark. */
  signerName: string;
}) {
  const adopt = useAdoptMySignature();
  const [tab, setTab] = useState<'drawn' | 'typed'>(
    adopted?.kind === 'typed' ? 'typed' : 'drawn'
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Your signature mark
        </p>
        {adopted?.adopted ? (
          <StatusPill tone="success">Adopted</StatusPill>
        ) : (
          <StatusPill tone="amber">Not adopted</StatusPill>
        )}
      </div>

      <p className="text-caption text-slate leading-relaxed">
        The mark is what appears in the box on the page. What makes the signature
        hold up is the cryptographic signature over the figures digest and the
        four evidential lines printed beside it — your name, officer title,
        permanent signer ID and the signing time. Re-adopting changes future
        signatures only; it never alters a mark already on a filed return.
      </p>

      <div role="tablist" aria-label="Signature style" className="flex items-center gap-1">
        <TabButton
          active={tab === 'drawn'}
          onClick={() => setTab('drawn')}
          Icon={PenTool}
          label="Draw it"
        />
        <TabButton
          active={tab === 'typed'}
          onClick={() => setTab('typed')}
          Icon={Type}
          label="Type it"
        />
      </div>

      {tab === 'drawn' ? (
        <DrawPad
          pending={adopt.isPending}
          onAdopt={(imagePngBase64) => adopt.mutate({ kind: 'drawn', imagePngBase64 })}
        />
      ) : (
        <TypeIt
          defaultName={adopted?.typedName ?? signerName}
          defaultFont={adopted?.typedFont ?? DEFAULT_TYPED_FONT}
          availableFonts={adopted?.availableFonts ?? Object.keys(TYPED_FONT_LABELS)}
          pending={adopt.isPending}
          onAdopt={(typedName, typedFont) =>
            adopt.mutate({ kind: 'typed', typedName, typedFont })
          }
        />
      )}

      {adopt.error != null && (
        <ErrorPanel error={adopt.error} title="That mark was not accepted" />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  Icon: typeof PenTool;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium rounded-md border ${
        active
          ? 'border-action/30 bg-action-light/50 text-action'
          : 'border-border text-slate hover:bg-surface'
      }`}
    >
      <Icon size={13} aria-hidden />
      {label}
    </button>
  );
}

function DrawPad({
  pending,
  onAdopt,
}: {
  pending: boolean;
  onAdopt: (imagePngBase64: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const [hasInk, setHasInk] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    // Transparent, because the mark is stamped over the page: a white plate
    // would erase whatever the field box sits on.
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.lineWidth = 4;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    // Deliberately a concrete hex, NOT rgb(var(--heading)): canvas colors
    // cannot resolve CSS variables, and the exported PNG's exact pixels are
    // what the service stamps onto the (white) filed page — theme-following
    // ink adopted in dark mode would file a near-white, unreadable mark. The
    // pad below is bg-white in both themes precisely so this dark ink stays
    // visible while drawing.
    context.strokeStyle = '#0A2540';
  }, []);

  const point = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - bounds.left) / bounds.width) * canvas.width,
      y: ((event.clientY - bounds.top) / bounds.height) * canvas.height,
    };
  };

  const start = (event: React.PointerEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    const context = canvasRef.current?.getContext('2d');
    if (!context) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drawing.current = true;
    const { x, y } = point(event);
    context.beginPath();
    context.moveTo(x, y);
    // A tap with no travel is a dot, and a dot is a legitimate mark.
    context.lineTo(x, y);
    context.stroke();
    setHasInk(true);
  };

  const extend = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return;
    const context = canvasRef.current?.getContext('2d');
    if (!context) return;
    const { x, y } = point(event);
    context.lineTo(x, y);
    context.stroke();
  };

  const stop = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    drawing.current = false;
  };

  const clear = () => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    setHasInk(false);
  };

  const save = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    onAdopt(canvas.toDataURL('image/png').replace(/^data:image\/png;base64,/, ''));
  };

  return (
    <div className="space-y-2">
      <canvas
        ref={canvasRef}
        width={PAD_WIDTH}
        height={PAD_HEIGHT}
        aria-label="Draw your signature"
        data-testid="signature-pad"
        onPointerDown={start}
        onPointerMove={extend}
        onPointerUp={stop}
        onPointerCancel={stop}
        className="w-full h-auto rounded border border-border bg-white touch-none cursor-crosshair"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!hasInk || pending}
          onClick={save}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
        >
          {pending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <Check size={13} aria-hidden />
          )}
          Adopt this mark
        </button>
        <button
          type="button"
          onClick={clear}
          disabled={pending}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-60"
        >
          <Eraser size={13} aria-hidden />
          Clear
        </button>
      </div>
    </div>
  );
}

function TypeIt({
  defaultName,
  defaultFont,
  availableFonts,
  pending,
  onAdopt,
}: {
  defaultName: string;
  defaultFont: NonNullable<AdoptSignatureRequestTypedFont>;
  availableFonts: string[];
  pending: boolean;
  onAdopt: (typedName: string, typedFont: NonNullable<AdoptSignatureRequestTypedFont>) => void;
}) {
  const [name, setName] = useState(defaultName);
  const [font, setFont] =
    useState<NonNullable<AdoptSignatureRequestTypedFont>>(defaultFont);

  return (
    <div className="space-y-2.5">
      <label className="block">
        <span className="block text-caption font-medium text-navy mb-1.5">
          Name as it should appear
        </span>
        <input
          type="text"
          value={name}
          maxLength={120}
          onChange={(event) => setName(event.target.value)}
          className="w-full rounded border border-border bg-surface px-3 py-2 text-body text-navy"
        />
      </label>

      <fieldset>
        <legend className="text-caption font-medium text-navy mb-1.5">Style</legend>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {availableFonts.map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={font === key}
              onClick={() => setFont(key as NonNullable<AdoptSignatureRequestTypedFont>)}
              className={`rounded border px-3 py-2.5 text-left ${
                font === key
                  ? 'border-action/40 bg-action-light/40'
                  : 'border-border hover:bg-surface'
              }`}
            >
              <span
                className="block truncate text-navy"
                style={{ ...TYPED_FONT_STYLES[key], fontSize: 20 }}
              >
                {name.trim() || 'Your name'}
              </span>
              <span className="mt-0.5 block text-micro uppercase tracking-wider text-slate">
                {TYPED_FONT_LABELS[key] ?? key}
              </span>
            </button>
          ))}
        </div>
      </fieldset>

      <p className="text-caption text-slate leading-relaxed">
        The script faces are embedded into the signed PDF from font files the
        platform ships under the SIL Open Font License, so the mark you pick here
        is the mark the regulator receives. The faces marked <em>typeset</em> are
        PDF standard-14 names the reader supplies — plainer, and there for a
        deployment that carries no font files.
      </p>

      <button
        type="button"
        disabled={pending || name.trim().length === 0}
        onClick={() => onAdopt(name.trim(), font)}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
      >
        {pending ? (
          <Loader2 size={13} className="animate-spin" aria-hidden />
        ) : (
          <Check size={13} aria-hidden />
        )}
        Adopt this mark
      </button>
    </div>
  );
}
