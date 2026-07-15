import type { ReactNode } from 'react';

export default function SectionLabel({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={`text-xs font-semibold uppercase tracking-[0.18em] text-accent ${className}`}
    >
      {children}
    </p>
  );
}
