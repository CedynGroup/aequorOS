import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';

/**
 * The shared control class. Exported for back-compat so the `inputClass`
 * string that was copy-pasted across desk pages resolves to one definition.
 */
export const inputClass =
  'w-full rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink placeholder:text-slate-light focus:border-focus focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

/**
 * Labeled field wrapper: a caption label, the control, and optional hint /
 * error lines. This is the console's real form system — pass any control (or
 * one of the primitives below) as children.
 */
export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  children,
  className = '',
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label htmlFor={htmlFor} className={`block ${className}`}>
      {label && (
        <span className="mb-1 block text-caption font-medium text-slate">
          {label}
          {required && <span className="ml-0.5 text-critical">*</span>}
        </span>
      )}
      {children}
      {error ? (
        <FormError>{error}</FormError>
      ) : (
        hint && <span className="mt-1 block text-micro text-slate-light">{hint}</span>
      )}
    </label>
  );
}

/** Inline validation / submission error line. */
export function FormError({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <span role="alert" className="mt-1 block text-caption text-critical">
      {children}
    </span>
  );
}

export function Input({
  invalid = false,
  className = '',
  ...rest
}: { invalid?: boolean } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`${inputClass} ${invalid ? 'border-critical focus:border-critical' : ''} ${className}`}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function Textarea({
  invalid = false,
  className = '',
  ...rest
}: { invalid?: boolean } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={`${inputClass} ${invalid ? 'border-critical focus:border-critical' : ''} ${className}`}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function Select({
  invalid = false,
  className = '',
  children,
  ...rest
}: { invalid?: boolean } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={`${inputClass} ${invalid ? 'border-critical focus:border-critical' : ''} ${className}`}
      aria-invalid={invalid || undefined}
      {...rest}
    >
      {children}
    </select>
  );
}
