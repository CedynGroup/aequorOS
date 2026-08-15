'use client';

import { useId, type ReactNode } from 'react';
import { Info } from 'lucide-react';

export type InfoTipPlacement = 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end';

const PLACEMENT: Record<InfoTipPlacement, string> = {
  'bottom-start': 'top-full left-0 mt-2',
  'bottom-end': 'top-full right-0 mt-2',
  'top-start': 'bottom-full left-0 mb-2',
  'top-end': 'bottom-full right-0 mb-2',
};

/**
 * Keyboard-focusable explainer, matching the shell's `role="tooltip"` pattern
 * (dark chip on the always-dark nav surface). Text-only content; the trigger
 * is a focusable info affordance so the copy is reachable without a mouse.
 * Ported from the dashboard's ImpliedRatingCard InfoTip.
 */
export function InfoTip({
  label,
  placement = 'bottom-start',
  width = 'w-72',
  children,
}: {
  label: string;
  placement?: InfoTipPlacement;
  width?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <span className="group/tip relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="inline-flex items-center justify-center text-slate transition-colors hover:text-action focus-visible:text-action"
      >
        <Info size={12} aria-hidden />
      </button>
      <span
        role="tooltip"
        id={id}
        className={`pointer-events-none absolute z-50 ${width} rounded border border-white/15 bg-nav px-3 py-2 text-caption font-normal normal-case leading-relaxed tracking-normal text-white/90 opacity-0 shadow-pop transition-opacity duration-150 group-hover/tip:opacity-100 group-focus-within/tip:opacity-100 ${PLACEMENT[placement]}`}
      >
        {children}
      </span>
    </span>
  );
}
