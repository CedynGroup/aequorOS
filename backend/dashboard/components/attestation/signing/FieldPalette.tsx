'use client';

/**
 * The palette rail: pick whose field it is, then put the field on the form.
 *
 * A BoG attestation block prints four lines per officer — "Prepared by (name /
 * designation / signature / date)" — and the platform used to model one box per
 * signer, so the form was not completable. Here each kind is its own draggable
 * chip and the signer drops it on the line the regulator printed for it.
 *
 * Two ways to place, because one of them has to work without a mouse: drag a
 * chip onto the page, or click it to arm and then click where it goes. The armed
 * mode is not a fallback for the drag — it is the keyboard-reachable path, and a
 * signature field on a document filed with the regulator is not something to put
 * out of reach of a signer who cannot drag.
 *
 * The recipient selector is what makes the preparer's job possible at all: the
 * DocMDP certification permits no new field afterwards, so the approver's boxes
 * have to be placed HERE, by the preparer, before the first signature lands.
 */

import type { PlacementFieldType } from '@aequoros/risk-service-api';
import { PenLine, Type, Briefcase, CalendarDays, Signature } from 'lucide-react';
import {
  DEFAULT_FIELD_SIZES,
  FIELD_TYPE_LABELS,
  PLACEABLE_FIELD_TYPES,
  derivedPreview,
  type SignerPreview,
} from '@/lib/attestation/fields';
import type { PlacementLimits } from '@/lib/attestation/geometry';

/** The MIME type the drag carries. Namespaced so nothing else claims a drop. */
export const FIELD_DRAG_TYPE = 'application/x-aequoros-signature-field';

const FIELD_ICONS: Record<PlacementFieldType, typeof PenLine> = {
  signature: Signature,
  name: Type,
  title: Briefcase,
  date_signed: CalendarDays,
  initials: PenLine,
};

export interface PaletteRecipient {
  signingRole: string;
  /** "You — Ama Mensah" or "Approver — to be named". */
  label: string;
  signer: SignerPreview | null;
  /** Tailwind border/background tokens, so a box matches its rail entry. */
  tone: string;
}

export default function FieldPalette({
  recipients,
  activeRole,
  onActiveRole,
  armed,
  onArm,
  limits,
  disabled,
}: {
  recipients: PaletteRecipient[];
  activeRole: string;
  onActiveRole: (role: string) => void;
  /** The kind waiting for a click on the page, or null. */
  armed: PlacementFieldType | null;
  onArm: (fieldType: PlacementFieldType | null) => void;
  limits: PlacementLimits;
  disabled: boolean;
}) {
  const active = recipients.find((recipient) => recipient.signingRole === activeRole);

  return (
    <div className="space-y-3" data-testid="field-palette">
      <div>
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Placing fields for
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1.5" role="radiogroup" aria-label="Recipient">
          {recipients.map((recipient) => (
            <button
              key={recipient.signingRole}
              type="button"
              role="radio"
              aria-checked={recipient.signingRole === activeRole}
              data-recipient-role={recipient.signingRole}
              disabled={disabled}
              onClick={() => onActiveRole(recipient.signingRole)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-caption font-medium disabled:opacity-50 ${
                recipient.signingRole === activeRole
                  ? `${recipient.tone} text-navy`
                  : 'border-border text-slate hover:bg-surface'
              }`}
            >
              <span
                aria-hidden
                className={`w-2 h-2 rounded-full border ${recipient.tone}`}
              />
              {recipient.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Drag onto the form
        </p>
        <ul className="mt-1.5 grid grid-cols-1 gap-1.5">
          {PLACEABLE_FIELD_TYPES.map((fieldType) => {
            const Icon = FIELD_ICONS[fieldType];
            const floor = limits[fieldType];
            const preview = derivedPreview(fieldType, active?.signer ?? null);
            const size = DEFAULT_FIELD_SIZES[fieldType];
            return (
              <li key={fieldType}>
                <button
                  type="button"
                  draggable={!disabled}
                  disabled={disabled}
                  aria-pressed={armed === fieldType}
                  data-field-type={fieldType}
                  onDragStart={(event) => {
                    event.dataTransfer.setData(FIELD_DRAG_TYPE, fieldType);
                    event.dataTransfer.effectAllowed = 'copy';
                    onArm(fieldType);
                  }}
                  onDragEnd={() => onArm(null)}
                  onClick={() => onArm(armed === fieldType ? null : fieldType)}
                  className={`w-full text-left rounded border px-2.5 py-2 disabled:opacity-50 ${
                    armed === fieldType
                      ? 'border-action bg-action/10'
                      : 'border-border bg-surface hover:bg-surface-alt'
                  } ${disabled ? '' : 'cursor-grab active:cursor-grabbing'}`}
                >
                  <span className="flex items-center gap-2">
                    <Icon size={14} className="shrink-0 text-slate" aria-hidden />
                    <span className="text-body font-medium text-navy">
                      {FIELD_TYPE_LABELS[fieldType]}
                    </span>
                    {floor && (
                      <span className="ml-auto font-mono text-micro text-slate tnum">
                        min {floor.minBoxWidth}×{floor.minBoxHeight}
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-caption text-slate truncate">
                    {fieldType === 'signature'
                      ? `Your adopted mark · ${size.width}×${size.height} pt`
                      : (preview ?? 'Filled from the signature record')}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <p className="text-caption text-slate leading-relaxed">
        {armed
          ? `Click the page to drop the ${FIELD_TYPE_LABELS[armed].toLowerCase()} field, or press Escape to cancel.`
          : 'Every value except the signature is filled from the signature record ' +
            'when it is signed — nothing here is typed, so the field only decides ' +
            'where it prints.'}
      </p>
    </div>
  );
}
