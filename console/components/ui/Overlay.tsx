'use client';

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Shared overlay behavior: focus trap, ESC to close, body scroll-lock, and
 * focus restoration on unmount. Used by both Modal and Drawer so every console
 * overlay is accessible by default (the one bespoke modal in the old sources
 * had none of this).
 */
function useOverlayBehavior(
  open: boolean,
  onClose: () => void,
  ref: RefObject<HTMLElement>,
) {
  // Hold onClose in a ref so an inline `onClose={() => ...}` from the caller (a
  // new function each render) doesn't re-run this effect on every keystroke —
  // which would re-fire the initial-focus and steal focus back from whatever
  // field the user is typing in. The effect must run only when `open` changes.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const node = ref.current;
    const focusables = () =>
      node ? Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)) : [];

    // Focus the first control (or the panel itself as a fallback).
    window.requestAnimationFrame(() => {
      const items = focusables();
      (items[0] ?? node)?.focus();
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        node?.focus();
        return;
      }
      const idx = items.indexOf(document.activeElement as HTMLElement);
      if (e.shiftKey && idx <= 0) {
        e.preventDefault();
        items[items.length - 1].focus();
      } else if (!e.shiftKey && idx === items.length - 1) {
        e.preventDefault();
        items[0].focus();
      }
    };

    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open, ref]);
}

/** SSR-safe portal: only renders after mount so `document` is available. */
function usePortal(open: boolean): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted && open;
}

type ModalSize = 'sm' | 'md' | 'lg' | 'xl';
const MODAL_WIDTH: Record<ModalSize, string> = {
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-3xl',
  xl: 'max-w-5xl',
};

/**
 * Accessible modal dialog: focus-trapped, ESC-closable, `aria-modal`,
 * scroll-locked, backdrop-dismissable (unless `dismissOnBackdrop={false}`).
 */
export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  size = 'md',
  dismissOnBackdrop = true,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  size?: ModalSize;
  dismissOnBackdrop?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  useOverlayBehavior(open, onClose, ref);
  const show = usePortal(open);
  if (!show) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 py-[10vh]">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={dismissOnBackdrop ? onClose : undefined}
        aria-hidden
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={`card relative flex max-h-[80vh] w-full ${MODAL_WIDTH[size]} flex-col overflow-hidden shadow-pop`}
      >
        {(title || description) && (
          <div className="flex items-start justify-between gap-4 border-b border-border-light px-5 py-4">
            <div className="min-w-0">
              {title && (
                <h2 id={titleId} className="text-h3 text-navy">
                  {title}
                </h2>
              )}
              {description && (
                <p id={descId} className="mt-0.5 text-caption text-slate">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="-mr-1 shrink-0 rounded p-1 text-slate hover:bg-surface hover:text-ink"
            >
              <X size={16} aria-hidden />
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border-light bg-surface/60 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Alias — `Dialog` reads better at some call sites. */
export const Dialog = Modal;

type DrawerSide = 'right' | 'left';

/**
 * Accessible side sheet: same focus-trap / ESC / scroll-lock behavior as Modal,
 * anchored to an edge. For contextual detail panels (inspector, provenance).
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  footer,
  side = 'right',
  width = 'w-[440px]',
  dismissOnBackdrop = true,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  side?: DrawerSide;
  /** Tailwind width class for the panel. */
  width?: string;
  dismissOnBackdrop?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  useOverlayBehavior(open, onClose, ref);
  const show = usePortal(open);
  if (!show) return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={dismissOnBackdrop ? onClose : undefined}
        aria-hidden
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={`absolute inset-y-0 ${side === 'right' ? 'right-0' : 'left-0'} flex ${width} max-w-[92vw] flex-col overflow-hidden border-border-light bg-surface-raised shadow-pop ${
          side === 'right' ? 'border-l' : 'border-r'
        }`}
      >
        {(title || description) && (
          <div className="flex items-start justify-between gap-4 border-b border-border-light px-5 py-4">
            <div className="min-w-0">
              {title && (
                <h2 id={titleId} className="text-h3 text-navy">
                  {title}
                </h2>
              )}
              {description && (
                <p id={descId} className="mt-0.5 text-caption text-slate">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close panel"
              className="-mr-1 shrink-0 rounded p-1 text-slate hover:bg-surface hover:text-ink"
            >
              <X size={16} aria-hidden />
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-border-light bg-surface/60 px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Alias — `Sheet` reads better at some call sites. */
export const Sheet = Drawer;
