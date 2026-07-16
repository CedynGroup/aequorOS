'use client';

/**
 * Zero-dependency spotlight tour over the app shell.
 *
 * Mechanics: a fixed full-viewport overlay; the highlight box sits over the
 * current target's getBoundingClientRect() and casts a giant
 * `0 0 0 9999px` box-shadow, which dims everything except the cutout. The
 * step card floats next to the target (right → below → above, clamped to the
 * viewport) with next/back/skip controls and step dots.
 *
 * Triggers: `?tour=1` in the URL starts it immediately (and strips the
 * param); otherwise, while the 'aeq-tour-done' localStorage flag is absent, a
 * dismissable "Take the tour" pill floats bottom-right. Esc exits, the card
 * traps focus, and target rects recompute on scroll/resize.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Compass, X } from 'lucide-react';
import { TOUR_STEPS, type TourStep } from './steps';

const DONE_KEY = 'aeq-tour-done';

type Rect = { top: number; left: number; width: number; height: number };

/** First visible element matching one of the step's selectors, else null. */
function findTarget(step: TourStep): HTMLElement | null {
  for (const selector of step.selectors) {
    let el: HTMLElement | null = null;
    try {
      el = document.querySelector<HTMLElement>(selector);
    } catch {
      continue; // e.g. :has() unsupported — try the next candidate
    }
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return el;
  }
  return null;
}

function markDone() {
  try {
    window.localStorage.setItem(DONE_KEY, '1');
  } catch {
    // storage unavailable — the tour just stays offerable this session
  }
}

export default function GuidedTour() {
  const [active, setActive] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [showPill, setShowPill] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLElement | null>(null);

  const step = TOUR_STEPS[stepIndex];

  const start = useCallback(() => {
    setStepIndex(0);
    setActive(true);
    setShowPill(false);
  }, []);

  const finish = useCallback(() => {
    setActive(false);
    setShowPill(false);
    markDone();
  }, []);

  const dismissPill = useCallback(() => {
    setShowPill(false);
    markDone();
  }, []);

  // Entry triggers: ?tour=1 (then strip it) or the first-visit pill.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('tour') === '1') {
      params.delete('tour');
      const query = params.toString();
      window.history.replaceState(
        null,
        '',
        `${window.location.pathname}${query ? `?${query}` : ''}`
      );
      start();
      return;
    }
    try {
      if (!window.localStorage.getItem(DONE_KEY)) setShowPill(true);
    } catch {
      // storage unavailable — skip the pill rather than nag every render
    }
  }, [start]);

  // Resolve the current step's target and keep its rect fresh on
  // scroll/resize (sidebar collapse animates width, so also poll one rAF).
  useEffect(() => {
    if (!active || !step) return;

    const measure = () => {
      const el = targetRef.current;
      if (!el || !el.isConnected) {
        setRect(null);
        return;
      }
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) {
        setRect(null);
        return;
      }
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };

    targetRef.current = findTarget(step);
    if (targetRef.current) {
      targetRef.current.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    measure();

    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [active, step]);

  // Esc exits; focus moves into the card on each step.
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        finish();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [active, finish]);

  useEffect(() => {
    if (!active) return;
    const frame = requestAnimationFrame(() => {
      const focusable = cardRef.current?.querySelector<HTMLElement>(
        'button[data-tour-primary]'
      );
      focusable?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [active, stepIndex]);

  // Simple focus trap: Tab cycles within the card.
  const trapFocus = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || !cardRef.current) return;
    const focusables = Array.from(
      cardRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
      )
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const current = document.activeElement;
    if (e.shiftKey && (current === first || !cardRef.current.contains(current))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && current === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  // Card placement: right of target → below → above, clamped to viewport.
  const cardStyle = useMemo<React.CSSProperties>(() => {
    const CARD_W = 340;
    const CARD_H = 240; // conservative estimate; clamped anyway
    const GAP = 14;
    if (typeof window === 'undefined' || !rect) {
      return {
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: CARD_W,
      };
    }
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const clamp = (v: number, min: number, max: number) =>
      Math.min(Math.max(v, min), Math.max(min, max));

    // Right placement (sidebar anchors).
    if (rect.left + rect.width + GAP + CARD_W <= vw - 16) {
      return {
        top: clamp(rect.top, 16, vh - CARD_H - 16),
        left: rect.left + rect.width + GAP,
        width: CARD_W,
      };
    }
    // Below, else above.
    const left = clamp(rect.left + rect.width / 2 - CARD_W / 2, 16, vw - CARD_W - 16);
    if (rect.top + rect.height + GAP + CARD_H <= vh - 16) {
      return { top: rect.top + rect.height + GAP, left, width: CARD_W };
    }
    return {
      top: clamp(rect.top - GAP - CARD_H, 16, vh - CARD_H - 16),
      left,
      width: CARD_W,
    };
  }, [rect]);

  if (!active) {
    if (!showPill) return null;
    return (
      <div className="no-print fixed bottom-5 right-5 z-[60] flex items-center gap-1 rounded-full bg-nav text-white shadow-pop pl-4 pr-1.5 py-1.5">
        <Compass size={14} className="text-action shrink-0" aria-hidden />
        <button
          type="button"
          onClick={start}
          className="px-1.5 py-1 text-caption font-medium hover:text-action transition-colors"
        >
          Take the tour
        </button>
        <button
          type="button"
          onClick={dismissPill}
          aria-label="Dismiss tour prompt"
          className="w-7 h-7 inline-flex items-center justify-center rounded-full text-white/60 hover:text-white hover:bg-white/10 transition-colors"
        >
          <X size={13} aria-hidden />
        </button>
      </div>
    );
  }

  const isFirst = stepIndex === 0;
  const isLast = stepIndex === TOUR_STEPS.length - 1;

  return (
    <div className="no-print fixed inset-0 z-[70]" role="presentation">
      {/* Spotlight cutout — the box-shadow dims everything around it. */}
      {rect ? (
        <div
          aria-hidden
          className="fixed rounded-lg border border-action/70 transition-all duration-150 ease-out pointer-events-none"
          style={{
            top: rect.top - 6,
            left: rect.left - 6,
            width: rect.width + 12,
            height: rect.height + 12,
            boxShadow: '0 0 0 9999px rgb(var(--nav-bg) / 0.72)',
          }}
        />
      ) : (
        <div
          aria-hidden
          className="fixed inset-0"
          style={{ backgroundColor: 'rgb(var(--nav-bg) / 0.72)' }}
        />
      )}

      {/* Click shield: swallow interactions with the page while touring. */}
      <div className="fixed inset-0" onClick={(e) => e.stopPropagation()} />

      {/* Step card */}
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Tour step ${stepIndex + 1} of ${TOUR_STEPS.length}: ${step.title}`}
        onKeyDown={trapFocus}
        className="fixed bg-surface-raised border border-border-light rounded-lg shadow-pop p-5"
        style={cardStyle}
      >
        <div className="flex items-start justify-between gap-3">
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Step {stepIndex + 1} of {TOUR_STEPS.length}
          </p>
          <button
            type="button"
            onClick={finish}
            aria-label="Exit tour"
            className="w-6 h-6 -mt-1 -mr-1 inline-flex items-center justify-center rounded text-slate hover:text-navy hover:bg-surface transition-colors"
          >
            <X size={13} aria-hidden />
          </button>
        </div>

        <h2 className="mt-1.5 text-h3 text-navy">{step.title}</h2>
        <p className="mt-1.5 text-body text-slate leading-relaxed">
          {step.body}
        </p>

        <div className="mt-4 flex items-center justify-between gap-3">
          {/* Step dots */}
          <div className="flex items-center gap-1.5" aria-hidden>
            {TOUR_STEPS.map((s, i) => (
              <span
                key={s.id}
                className={`rounded-full transition-all ${
                  i === stepIndex
                    ? 'w-4 h-1.5 bg-action'
                    : 'w-1.5 h-1.5 bg-border'
                }`}
              />
            ))}
          </div>

          <div className="flex items-center gap-2">
            {!isLast && (
              <button
                type="button"
                onClick={finish}
                className="px-2.5 py-1.5 text-caption font-medium text-slate hover:text-navy transition-colors"
              >
                Skip
              </button>
            )}
            {!isFirst && (
              <button
                type="button"
                onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
                className="px-3 py-1.5 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface transition-colors"
              >
                Back
              </button>
            )}
            <button
              type="button"
              data-tour-primary
              onClick={() =>
                isLast ? finish() : setStepIndex((i) => i + 1)
              }
              className="btn-primary px-4 py-1.5 text-caption font-medium"
            >
              {isLast ? 'Finish' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
