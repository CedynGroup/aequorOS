import Link from 'next/link';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';

type Variant =
  | 'primary'
  | 'secondary'
  | 'primary-on-dark'
  | 'secondary-on-light';

const base =
  'inline-flex items-center justify-center rounded px-6 h-12 text-[15px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2';

const variants: Record<Variant, string> = {
  primary:
    'bg-navy-deep text-white hover:bg-navy focus-visible:ring-offset-white',
  'primary-on-dark':
    'bg-white text-navy-deep hover:bg-ice-blue focus-visible:ring-offset-navy-deep',
  secondary:
    'border border-white/35 text-white hover:bg-white/10 focus-visible:ring-offset-navy-deep',
  'secondary-on-light':
    'border border-[#C9CDD8] text-ink hover:border-navy-deep focus-visible:ring-offset-white',
};

type CommonProps = {
  variant?: Variant;
  children: ReactNode;
  className?: string;
};

type LinkButtonProps = CommonProps & {
  href: string;
  external?: boolean;
};

type NativeButtonProps = CommonProps &
  Omit<ComponentPropsWithoutRef<'button'>, 'children' | 'className'>;

export function LinkButton({
  href,
  variant = 'primary',
  children,
  className = '',
  external = false,
}: LinkButtonProps) {
  const classes = `${base} ${variants[variant]} ${className}`;
  if (external) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={classes}
      >
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={classes}>
      {children}
    </Link>
  );
}

export default function Button({
  variant = 'primary',
  children,
  className = '',
  ...rest
}: NativeButtonProps) {
  return (
    <button className={`${base} ${variants[variant]} ${className}`} {...rest}>
      {children}
    </button>
  );
}
