'use client';

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

/** Copy-to-clipboard affordance with a 1.5s confirmation tick. */
export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label ?? `Copy ${value}`}
      title={label ?? 'Copy'}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex items-center rounded p-1 text-slate hover:bg-surface hover:text-ink"
    >
      {copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
    </button>
  );
}

/** Platform id (BK-/OR-) in mono with an inline copy button. */
export function MonoId({ id, className = '' }: { id: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-0.5 ${className}`}>
      <span className="font-mono text-caption text-ink">{id}</span>
      <CopyButton value={id} />
    </span>
  );
}
