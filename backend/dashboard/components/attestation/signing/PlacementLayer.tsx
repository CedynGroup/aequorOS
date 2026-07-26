'use client';

/**
 * The placed fields, on the page, where they will actually print.
 *
 * A field box is not decoration: `pdf_signing` creates a real AcroForm field at
 * these coordinates on the archived unsigned artifact, and the certification's
 * DocMDP policy means no field can be added or moved afterwards. So every box —
 * both signers', every kind — has to be right BEFORE the preparer signs, which
 * is the whole reason this layer exists rather than a constant in the backend.
 *
 * Each box shows what it will print: the signer's adopted mark in a signature
 * field, and the derived value (their real name, real designation, today's date)
 * in the others. The preview is computed the same way the backend computes the
 * stamp, so what a signer sees here is what an examiner reads on the filing.
 *
 * Pointer events, not mouse events: the same code then works for a trackpad, a
 * mouse, a pen and a touch screen, and `setPointerCapture` keeps a drag alive
 * when the pointer leaves the box — which on a trackpad it constantly does.
 * Arrow keys nudge a focused box and Delete removes it, because a signature
 * field on a filed document is not something to make unreachable without a
 * pointing device.
 */

import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { AlertTriangle, GripVertical, X } from 'lucide-react';
import type { AdoptedSignatureRead, SignatureFieldPlacement } from '@aequoros/risk-service-api';
import {
  clampRect,
  fieldTypeOf,
  type PageSpace,
  type ViewerRect,
} from '@/lib/attestation/geometry';
import {
  FIELD_TYPE_LABELS,
  derivedPreview,
  type SignerPreview,
} from '@/lib/attestation/fields';
import { DEFAULT_TYPED_FONT, TYPED_FONT_STYLES } from './fonts';

export interface FieldSlot {
  /** Index into the workspace's placement list — the box's identity. */
  index: number;
  placement: SignatureFieldPlacement;
  /** "Preparer — Ama Mensah" as the box labels itself on the page. */
  ownerLabel: string;
  /** The caller's own field: it carries their adopted mark and is theirs to move. */
  mine: boolean;
  /**
   * This role has already signed, so the page underneath carries the real mark
   * and the real filled values. The box then frames what is there rather than
   * previewing a second copy of it.
   */
  signed: boolean;
  /** Who this box prints, as far as the browser knows. */
  signer: SignerPreview | null;
  violation: string | null;
}

export default function PlacementLayer({
  space,
  slots,
  editable,
  mark,
  onMove,
  onRemove,
}: {
  space: PageSpace;
  slots: FieldSlot[];
  /** False once a signature exists — the fields are part of a certified revision. */
  editable: boolean;
  /** The caller's adopted mark, previewed inside their own signature box. */
  mark: AdoptedSignatureRead | undefined;
  onMove: (index: number, rect: ViewerRect) => void;
  onRemove: (index: number) => void;
}) {
  return (
    <div className="absolute inset-0">
      {slots.map((slot) => {
        if (slot.placement.pageIndex !== space.pageIndex) return null;
        return (
          <FieldBox
            key={slot.index}
            slot={slot}
            rect={space.toViewer(slot.placement)}
            space={space}
            editable={editable}
            mark={slot.mine ? mark : undefined}
            onMove={(rect) => onMove(slot.index, rect)}
            onRemove={() => onRemove(slot.index)}
          />
        );
      })}
    </div>
  );
}

type Drag =
  | { mode: 'move'; pointerX: number; pointerY: number; origin: ViewerRect }
  | { mode: 'resize'; pointerX: number; pointerY: number; origin: ViewerRect };

function FieldBox({
  slot,
  rect,
  space,
  editable,
  mark,
  onMove,
  onRemove,
}: {
  slot: FieldSlot;
  rect: ViewerRect;
  space: PageSpace;
  editable: boolean;
  mark: AdoptedSignatureRead | undefined;
  onMove: (rect: ViewerRect) => void;
  onRemove: () => void;
}) {
  const drag = useRef<Drag | null>(null);
  const [dragging, setDragging] = useState(false);
  const fieldType = fieldTypeOf(slot.placement);

  const begin = (mode: Drag['mode']) => (event: ReactPointerEvent<HTMLElement>) => {
    if (!editable) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { mode, pointerX: event.clientX, pointerY: event.clientY, origin: rect };
    setDragging(true);
  };

  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const state = drag.current;
    if (!state) return;
    const dx = event.clientX - state.pointerX;
    const dy = event.clientY - state.pointerY;
    const next: ViewerRect =
      state.mode === 'move'
        ? { ...state.origin, left: state.origin.left + dx, top: state.origin.top + dy }
        : {
            ...state.origin,
            width: state.origin.width + dx,
            height: state.origin.height + dy,
          };
    onMove(clampRect(next, space));
  };

  const end = (event: ReactPointerEvent<HTMLElement>) => {
    if (!drag.current) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    setDragging(false);
  };

  const onKey = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!editable) return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      onRemove();
      return;
    }
    const step = (event.shiftKey ? 10 : 1) * (space.cssHeight / space.heightPt);
    const shift =
      event.key === 'ArrowLeft'
        ? { x: -step, y: 0 }
        : event.key === 'ArrowRight'
          ? { x: step, y: 0 }
          : event.key === 'ArrowUp'
            ? { x: 0, y: -step }
            : event.key === 'ArrowDown'
              ? { x: 0, y: step }
              : null;
    if (!shift) return;
    event.preventDefault();
    onMove(clampRect({ ...rect, left: rect.left + shift.x, top: rect.top + shift.y }, space));
  };

  // Tints, not fills: the box sits ON the return, and a signer who cannot read
  // the line their signature is going onto is not reviewing the document. The
  // colours are the raw tokens rather than the `-light` surfaces, which are
  // opaque plates meant for the app chrome, not for overlaying paper.
  const tone = slot.violation
    ? 'border-critical bg-critical/10'
    : slot.mine
      ? 'border-action bg-action/10'
      : 'border-slate bg-slate/5';

  return (
    <div
      role="group"
      aria-label={`${slot.ownerLabel} ${FIELD_TYPE_LABELS[fieldType].toLowerCase()} field`}
      data-signing-role={slot.placement.signingRole}
      data-field-type={fieldType}
      // The box in PDF user space, mirrored into the DOM. The flip between the
      // two coordinate systems is silent when it is wrong — a signature simply
      // prints somewhere else — so the converted value is made observable and is
      // asserted against a known drag in e2e/attestation.spec.ts.
      data-pdf-box={`${slot.placement.x1},${slot.placement.y1},${slot.placement.x2},${slot.placement.y2}`}
      tabIndex={editable ? 0 : -1}
      onKeyDown={onKey}
      onPointerDown={begin('move')}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
      className={`absolute rounded-sm border-2 border-dashed ${tone} ${
        editable ? 'cursor-move touch-none' : 'cursor-default'
      } ${dragging ? 'opacity-90' : ''} outline-none focus-visible:ring-2 focus-visible:ring-focus`}
    >
      <span className="absolute -top-5 left-0 whitespace-nowrap rounded-t px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider bg-surface-raised border border-border-light text-navy">
        {FIELD_TYPE_LABELS[fieldType]} · {slot.ownerLabel}
      </span>

      {slot.signed ? null : fieldType === 'signature' ? (
        <MarkPreview mark={mark} height={rect.height} />
      ) : (
        <ValuePreview
          value={derivedPreview(fieldType, slot.signer)}
          height={rect.height}
        />
      )}

      {slot.violation && (
        <span
          role="alert"
          className="absolute -bottom-5 left-0 inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-micro font-medium bg-critical-light text-critical border border-critical/30"
        >
          <AlertTriangle size={10} aria-hidden />
          {slot.violation}
        </span>
      )}

      {editable && (
        <>
          <button
            type="button"
            aria-label={`Remove the ${slot.ownerLabel} ${FIELD_TYPE_LABELS[fieldType].toLowerCase()} field`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={onRemove}
            className="absolute -right-2 -top-2 w-4 h-4 rounded-full bg-surface-raised border border-border text-slate hover:text-critical inline-flex items-center justify-center"
          >
            <X size={9} aria-hidden />
          </button>
          <span
            onPointerDown={begin('resize')}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
            aria-hidden
            className="absolute -right-1 -bottom-1 w-4 h-4 rounded-sm bg-surface-raised border border-border text-slate flex items-center justify-center cursor-se-resize touch-none"
          >
            <GripVertical size={9} className="rotate-45" />
          </span>
        </>
      )}
    </div>
  );
}

/**
 * What the signer's mark will look like in the box. Drawn marks come back as
 * normalised PNG bytes; a typed mark carries no image, so it is previewed with
 * the CSS face closest to the PDF standard-14 the backend will draw it with.
 */
function MarkPreview({
  mark,
  height,
}: {
  mark: AdoptedSignatureRead | undefined;
  height: number;
}) {
  if (!mark?.adopted) {
    return (
      <span className="absolute inset-0 flex items-center justify-center text-micro text-slate">
        Sign here
      </span>
    );
  }
  if (mark.kind === 'drawn' && mark.imagePngBase64) {
    return (
      // A data: URI of the signer's own adopted mark, already in memory —
      // next/image would only add an optimizer hop for bytes we hold.
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={`data:image/png;base64,${mark.imagePngBase64}`}
        alt="Your adopted signature"
        className="absolute left-1 right-1 top-1/2 -translate-y-1/2 object-contain"
        style={{ height: Math.max(height * 0.8, 10) }}
      />
    );
  }
  return (
    <span
      className="absolute left-1.5 top-1/2 -translate-y-1/2 truncate text-navy"
      style={{
        ...TYPED_FONT_STYLES[mark.typedFont ?? DEFAULT_TYPED_FONT],
        fontSize: Math.max(height * 0.6, 9),
      }}
    >
      {mark.typedName}
    </span>
  );
}

/**
 * The exact string the backend will stamp, shown at roughly the size it will
 * print. `null` means the signer is not named yet — rendered as such rather than
 * as a plausible placeholder, because a placeholder that looks like a value is a
 * preview of something that will not appear.
 */
function ValuePreview({ value, height }: { value: string | null; height: number }) {
  return (
    <span
      className={`absolute left-1 right-1 top-1/2 -translate-y-1/2 truncate font-mono tnum ${
        value ? 'text-navy' : 'text-slate italic'
      }`}
      style={{ fontSize: Math.max(height * 0.55, 8) }}
    >
      {value ?? 'filled when signed'}
    </span>
  );
}
