import type { ReactNode } from 'react';
import { Check } from 'lucide-react';

export type StepStatus = 'complete' | 'current' | 'upcoming' | 'error';

export type Step = {
  key: string;
  label: ReactNode;
  description?: ReactNode;
  /** Explicit status override; otherwise derived from `current`. */
  status?: StepStatus;
};

/**
 * The one console stepper (replaces three hand-rolled variants). Derives each
 * step's status from `current` (the index of the active step) unless a step
 * carries an explicit `status`. Horizontal by default; vertical for maker-
 * checker ceremony rails.
 */
export function Stepper({
  steps,
  current = 0,
  orientation = 'horizontal',
  className = '',
}: {
  steps: Step[];
  current?: number;
  orientation?: 'horizontal' | 'vertical';
  className?: string;
}) {
  const statusFor = (i: number, explicit?: StepStatus): StepStatus =>
    explicit ?? (i < current ? 'complete' : i === current ? 'current' : 'upcoming');

  if (orientation === 'vertical') {
    return (
      <ol className={`space-y-0 ${className}`}>
        {steps.map((step, i) => {
          const status = statusFor(i, step.status);
          const isLast = i === steps.length - 1;
          return (
            <li key={step.key} className="relative flex gap-3 pb-6 last:pb-0">
              {!isLast && (
                <span
                  aria-hidden
                  className={`absolute left-[13px] top-7 h-[calc(100%-1.25rem)] w-px ${
                    status === 'complete' ? 'bg-action' : 'bg-border-light'
                  }`}
                />
              )}
              <StepMarker status={status} index={i} />
              <div className="min-w-0 pt-0.5">
                <p
                  className={`text-body font-medium ${
                    status === 'upcoming' ? 'text-slate' : 'text-navy'
                  }`}
                >
                  {step.label}
                </p>
                {step.description && (
                  <p className="mt-0.5 text-caption text-slate">{step.description}</p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <ol className={`flex items-center ${className}`}>
      {steps.map((step, i) => {
        const status = statusFor(i, step.status);
        const isLast = i === steps.length - 1;
        return (
          <li key={step.key} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-2.5">
              <StepMarker status={status} index={i} />
              <div className="min-w-0">
                <p
                  className={`whitespace-nowrap text-caption font-medium ${
                    status === 'upcoming' ? 'text-slate' : 'text-navy'
                  }`}
                >
                  {step.label}
                </p>
                {step.description && (
                  <p className="whitespace-nowrap text-micro text-slate-light">{step.description}</p>
                )}
              </div>
            </div>
            {!isLast && (
              <span
                aria-hidden
                className={`mx-3 h-px flex-1 ${status === 'complete' ? 'bg-action' : 'bg-border-light'}`}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepMarker({ status, index }: { status: StepStatus; index: number }) {
  const base =
    'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-caption font-semibold tnum';
  if (status === 'complete') {
    return (
      <span className={`${base} border-action bg-action text-white`} aria-label="Completed step">
        <Check size={14} aria-hidden />
      </span>
    );
  }
  if (status === 'current') {
    return (
      <span
        className={`${base} border-action bg-action-light text-action`}
        aria-current="step"
        aria-label="Current step"
      >
        {index + 1}
      </span>
    );
  }
  if (status === 'error') {
    return (
      <span className={`${base} border-critical bg-critical-light text-critical`} aria-label="Step needs attention">
        {index + 1}
      </span>
    );
  }
  return (
    <span className={`${base} border-border-light bg-surface text-slate`} aria-label="Upcoming step">
      {index + 1}
    </span>
  );
}
