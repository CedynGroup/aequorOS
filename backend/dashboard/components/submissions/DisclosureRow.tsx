'use client';

/**
 * A secondary section of the Returns workspace, collapsed to one line.
 *
 * The return is the subject of that screen: an officer is reading figures they
 * will personally attest to, and a supervisor may read the same screen. Workflow
 * bookkeeping — the certification detail, the channel trail, the earlier
 * versions — earns a line, not a permanent column beside the figures.
 *
 * The collapsed line always STATES ITS OWN STATUS. A row an officer has to open
 * to learn that there is nothing in it has cost them the click for nothing.
 */

import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export default function DisclosureRow({
  title,
  summary,
  children,
  defaultOpen = false,
  open: controlledOpen,
  onOpenChange,
  testId,
  tone = 'default',
  flush = false,
}: {
  title: string;
  /** What is in it, in a few words. Shown collapsed, so never "Details". */
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  /**
   * Controlled, for a row the surface has to open on the officer's behalf — the
   * certification row when they press the act in the command bar, because the
   * ceremony opens inside it and a collapsed row would swallow it.
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  testId?: string;
  /** `attention` draws the eye when the row is where the work is. */
  tone?: 'default' | 'attention';
  /**
   * The child is itself a card. Strips the inner padding and the child's own
   * frame so a collapsed row does not open into a card inside a card — two
   * borders and two titles for one thing.
   */
  flush?: boolean;
}) {
  const [ownOpen, setOwnOpen] = useState(defaultOpen);
  const open = controlledOpen ?? ownOpen;
  const setOpen = (next: boolean) => {
    setOwnOpen(next);
    onOpenChange?.(next);
  };
  return (
    <section
      data-testid={testId}
      className={`card overflow-hidden ${
        tone === 'attention' ? 'border-l-4 border-l-action' : ''
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-surface"
      >
        <span className="text-body font-medium text-navy whitespace-nowrap">
          {title}
        </span>
        <span className="min-w-0 flex-1 text-caption text-slate truncate">
          {summary}
        </span>
        <ChevronDown
          size={14}
          aria-hidden
          className={`shrink-0 text-slate transition-transform ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>
      {open && (
        <div
          className={
            flush
              ? 'border-t border-border-light [&>section]:rounded-none [&>section]:border-0 [&>section]:shadow-none'
              : 'px-5 pb-5 pt-1 border-t border-border-light'
          }
        >
          {children}
        </div>
      )}
    </section>
  );
}
