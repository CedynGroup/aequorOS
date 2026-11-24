"use client";

import { createContext, startTransition, useActionState, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { IDLE, type FormAction } from "@/lib/action-state";
import { button, cx } from "./ui";

const PendingContext = createContext<{ pending: boolean; intent: string | null }>({ pending: false, intent: null });

/**
 * A form bound to one server action. With JavaScript it submits in a
 * transition (so typed values survive an error) and announces the result in
 * a live region; without JavaScript the native form post still works.
 * Several outcomes share one action through the submit button's `intent`.
 */
export function ActionForm({
  action,
  children,
  className,
  label,
  feedbackClassName,
}: {
  action: FormAction;
  children: ReactNode;
  className?: string;
  label?: string;
  feedbackClassName?: string;
}) {
  const [state, formAction, pending] = useActionState(action, IDLE);
  const formRef = useRef<HTMLFormElement>(null);
  const [intent, setIntent] = useState<string | null>(null);

  useEffect(() => {
    if (state.status === "success" && state.reset) formRef.current?.reset();
  }, [state]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    setIntent(submitter?.value ?? null);
    const data = new FormData(event.currentTarget, submitter);
    startTransition(() => formAction(data));
  }

  return (
    <PendingContext.Provider value={{ pending, intent: pending ? intent : null }}>
      <form ref={formRef} action={formAction} onSubmit={onSubmit} aria-label={label} className={className} noValidate={false}>
        {children}
        <div className={cx("text-[13px] leading-[1.45]", feedbackClassName)}>
          <p role="status" aria-live="polite" className={cx("m-0", state.status === "success" ? "font-medium text-ok" : "sr-only")}>
            {state.status === "success" ? state.message : ""}
          </p>
          <p role="alert" className={cx("m-0", state.status === "error" ? "font-medium text-bad" : "sr-only")}>
            {state.status === "error" ? <><span className="font-semibold">Not done. </span>{state.message}</> : ""}
          </p>
        </div>
      </form>
    </PendingContext.Provider>
  );
}

export function SubmitButton({
  children,
  variant = "primary",
  intent,
  className,
  pendingLabel,
  skipValidation,
}: {
  children: ReactNode;
  variant?: keyof typeof button;
  intent?: string;
  className?: string;
  pendingLabel?: string;
  /** For an outcome that needs none of the form's required fields (e.g. "Close query"). */
  skipValidation?: boolean;
}) {
  const { pending, intent: active } = useContext(PendingContext);
  const mine = pending && (intent === undefined || active === intent || active === null);
  return (
    <button
      type="submit"
      name={intent ? "intent" : undefined}
      value={intent}
      formNoValidate={skipValidation}
      disabled={pending}
      aria-busy={mine || undefined}
      className={cx(button[variant], className)}
    >
      {mine && pendingLabel ? pendingLabel : children}
    </button>
  );
}
