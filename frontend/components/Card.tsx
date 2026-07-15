import type { ReactNode } from 'react';

export default function Card({
  children,
  className = '',
  accentBar = true,
}: {
  children: ReactNode;
  className?: string;
  accentBar?: boolean;
}) {
  return (
    <div
      className={`bg-white border border-border-light rounded-lg ${
        accentBar ? 'border-l-4 border-l-accent' : ''
      } ${className}`}
    >
      {children}
    </div>
  );
}
