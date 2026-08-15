import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'btn-primary text-white',
  secondary: 'border border-border bg-transparent text-ink hover:bg-surface',
  ghost: 'bg-transparent text-slate hover:bg-surface hover:text-ink',
  danger: 'bg-critical text-white hover:bg-critical/85',
};

const SIZE: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-caption gap-1',
  md: 'px-4 py-2 text-body gap-1.5',
  lg: 'px-5 py-2.5 text-body-lg gap-2',
};

/**
 * The single button primitive: variant × size, an optional leading icon, and a
 * `loading` state that swaps in a spinner and disables the control. Kills the
 * per-page `btn-primary`/bordered-button copy-paste.
 */
export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  children,
  className = '',
  disabled,
  type = 'button',
  ...rest
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  children?: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
      {...rest}
    >
      {loading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}
