'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Info, X, ArrowUpRight } from 'lucide-react';

const STORAGE_KEY = 'aequoros-demo-banner-dismissed';

export default function PrototypeBanner() {
  const [dismissed, setDismissed] = useState(true); // SSR-safe: hidden until effect runs

  useEffect(() => {
    try {
      const v = window.localStorage.getItem(STORAGE_KEY);
      setDismissed(v === '1');
    } catch {
      setDismissed(false);
    }
  }, []);

  const dismiss = () => {
    setDismissed(true);
    try {
      window.localStorage.setItem(STORAGE_KEY, '1');
    } catch {
      // ignore
    }
  };

  if (dismissed) return null;

  return (
    <div className="bg-nav text-white border-b border-white/10">
      <div className="px-4 md:px-6 py-2.5 flex items-center gap-3 text-caption">
        <Info size={14} className="text-action shrink-0" aria-hidden />
        <p className="flex-1 min-w-0">
          <span className="font-medium">Interactive prototype</span> ·
          Synthetic Bank of Ghana licensee data for{' '}
          <span className="font-medium">Sample Bank Limited</span> · Not a live
          system. For investor evaluation and bank validation use only.
        </p>
        <Link
          href="https://aequoros.com"
          className="hidden md:inline-flex items-center gap-1 text-action hover:text-white transition-colors"
        >
          aequoros.com
          <ArrowUpRight size={12} aria-hidden />
        </Link>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss banner"
          className="w-6 h-6 inline-flex items-center justify-center rounded text-white/60 hover:text-white hover:bg-white/10 shrink-0"
        >
          <X size={13} aria-hidden />
        </button>
      </div>
    </div>
  );
}
