import type { ReactNode } from 'react';

/**
 * Section kicker — italic serif in amber. The redesign's replacement for the
 * old ALL-CAPS eyebrow, which repeated identically above every section.
 */
export default function Kicker({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p className={`font-serif italic text-[17px] text-kicker ${className}`}>
      {children}
    </p>
  );
}
