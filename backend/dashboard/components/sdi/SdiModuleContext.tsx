'use client';

import { useModuleScope } from '@/components/shell/BankContext';

export default function SdiModuleContext({ title, children }: { title: string; children: React.ReactNode }) {
  const isSdi = useModuleScope().institutionClass === 'sdi';
  if (!isSdi) return null;
  return (
    <section className="mx-8 mt-6 border-l-2 border-action bg-surface-raised px-4 py-3">
      <p className="text-caption font-semibold text-navy">{title}</p>
      <p className="mt-1 text-caption leading-relaxed text-slate">{children}</p>
    </section>
  );
}