"use client";

/**
 * A modal shell for the ICAAP workspace's short forms.
 *
 * Kept local rather than global: it carries nothing but layout, focus and an
 * Escape key, and every ICAAP dialog is a small maker action (create a cycle,
 * add a block, upload evidence) where the server is the authority on whether
 * the action is allowed.
 */

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

export default function Dialog({
  title,
  description,
  onClose,
  footer,
  children,
  wide = false,
}: {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    panelRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button",
    )?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-12"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <div
        ref={panelRef}
        className={`relative w-full ${wide ? "max-w-3xl" : "max-w-lg"} card overflow-hidden`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border-light px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-h3 text-navy">{title}</h2>
            {description && (
              <div className="mt-1 text-caption text-slate">{description}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded p-1 text-slate hover:bg-surface hover:text-navy"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border-light bg-surface/60 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function PrimaryButton({
  children,
  disabled,
  onClick,
  type = "button",
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

export function SecondaryButton({
  children,
  disabled,
  onClick,
  title,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface hover:text-navy disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  );
}

/**
 * A labelled form control.
 *
 * The hint sits OUTSIDE the `<label>` on purpose: inside it, the hint becomes
 * part of the control's accessible NAME.
 *
 * A `<select>` nested in its label has the same problem and the hint cannot
 * fix it — the label's text content includes every OPTION, so the control
 * announces itself as "Financial year 2027 2026 2025 …". Every `<select>` in
 * this module therefore carries an explicit `aria-label`, which wins over the
 * wrapping label. Drop one and a screen reader reads the whole option list as
 * the field's name.
 */
export function FieldLabel({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="block">
        <span className="text-caption font-medium text-navy">{label}</span>
        {children}
      </label>
      {hint && <p className="mt-1 text-caption text-slate">{hint}</p>}
    </div>
  );
}

export const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-body text-ink focus:border-action focus:outline-none";
