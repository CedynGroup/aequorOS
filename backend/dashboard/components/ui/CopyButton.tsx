'use client';

/**
 * Copy-to-clipboard button. `dark` matches code/curl blocks (navy background);
 * the default light variant suits white cards (Settings, connection fields).
 */

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

export default function CopyButton({
  text,
  label,
  variant = 'light',
  className = '',
}: {
  text: string;
  label: string;
  variant?: 'light' | 'dark';
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const tone =
    variant === 'dark'
      ? 'border-white/20 text-white/70 hover:text-white hover:border-white/40'
      : 'border-border text-slate hover:text-navy hover:border-slate';
  return (
    <button
      type="button"
      aria-label={`Copy ${label}`}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-micro font-medium transition-colors ${tone} ${className}`}
    >
      {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
