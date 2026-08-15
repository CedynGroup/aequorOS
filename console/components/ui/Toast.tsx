'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';

export type ToastTone = 'ok' | 'warn' | 'crit' | 'info';

export type ToastInput = {
  title: ReactNode;
  description?: ReactNode;
  tone?: ToastTone;
  /** Auto-dismiss delay in ms; 0 keeps it until dismissed. */
  duration?: number;
};

type Toast = ToastInput & { id: number; tone: ToastTone };

type ToastApi = {
  toast: (input: ToastInput) => number;
  success: (title: ReactNode, opts?: Omit<ToastInput, 'title' | 'tone'>) => number;
  error: (title: ReactNode, opts?: Omit<ToastInput, 'title' | 'tone'>) => number;
  info: (title: ReactNode, opts?: Omit<ToastInput, 'title' | 'tone'>) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const TONE_STYLE: Record<ToastTone, { border: string; icon: ReactNode }> = {
  ok: { border: 'border-l-success', icon: <CheckCircle2 size={16} className="text-success" /> },
  warn: { border: 'border-l-warning', icon: <AlertTriangle size={16} className="text-warning" /> },
  crit: { border: 'border-l-critical', icon: <XCircle size={16} className="text-critical" /> },
  info: { border: 'border-l-action', icon: <Info size={16} className="text-action" /> },
};

/** Wrap the app once (AppShell does this). Exposes `useToast()` to descendants. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mounted, setMounted] = useState(false);
  const seq = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  useEffect(() => setMounted(true), []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (input: ToastInput): number => {
      const id = ++seq.current;
      const tone = input.tone ?? 'info';
      const duration = input.duration ?? (tone === 'crit' ? 7000 : 4500);
      setToasts((prev) => [...prev, { ...input, id, tone }]);
      if (duration > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), duration),
        );
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((t) => clearTimeout(t));
      map.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      toast: push,
      success: (title, opts) => push({ ...opts, title, tone: 'ok' }),
      error: (title, opts) => push({ ...opts, title, tone: 'crit' }),
      info: (title, opts) => push({ ...opts, title, tone: 'info' }),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      {mounted &&
        createPortal(
          <div
            className="pointer-events-none fixed right-4 top-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
            role="region"
            aria-label="Notifications"
          >
            {toasts.map((t) => (
              <div
                key={t.id}
                role="status"
                aria-live={t.tone === 'crit' ? 'assertive' : 'polite'}
                className={`card pointer-events-auto flex items-start gap-2.5 border-l-4 px-3.5 py-3 shadow-pop ${TONE_STYLE[t.tone].border}`}
              >
                <span className="mt-0.5 shrink-0">{TONE_STYLE[t.tone].icon}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-body font-medium text-navy">{t.title}</p>
                  {t.description && (
                    <p className="mt-0.5 break-words text-caption text-slate">{t.description}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => dismiss(t.id)}
                  aria-label="Dismiss notification"
                  className="-mr-1 shrink-0 rounded p-0.5 text-slate hover:bg-surface hover:text-ink"
                >
                  <X size={14} aria-hidden />
                </button>
              </div>
            ))}
          </div>,
          document.body,
        )}
    </ToastContext.Provider>
  );
}

/**
 * Access the toast API. Safe to call outside a provider — it degrades to a
 * no-op (returns -1) so a component never crashes for lack of a provider.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx) return ctx;
  const noop = () => -1;
  return { toast: noop, success: noop, error: noop, info: noop, dismiss: () => {} };
}
