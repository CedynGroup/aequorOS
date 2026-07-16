import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';

export type Feed = { label: string; href: string };

/**
 * Cross-link chip from a behavioral model to the ALM engine that consumes
 * its accepted assumptions (IRRBB, LCR, FTP, forecasting).
 */
export default function FeedsChip({ feed }: { feed: Feed }) {
  return (
    <Link
      href={feed.href}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-action/25 bg-action-light text-action text-micro font-medium uppercase tracking-wider hover:border-action/50 transition-colors"
    >
      {feed.label}
      <ArrowUpRight size={10} aria-hidden />
    </Link>
  );
}
