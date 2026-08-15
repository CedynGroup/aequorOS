'use client';

import { useState } from 'react';
import { Eye, Loader2, ShieldAlert } from 'lucide-react';
import { endInspectorSession } from '@/lib/api';
import { useInspector } from '@/lib/inspector';
import { relTime } from '@/lib/format';

/**
 * Un-dismissable strip shown while a Tenant Inspector session is active. The
 * only way to remove it is to end the session — the operator must always see
 * that they are acting inside another tenant's data.
 */
export default function ImpersonationBanner() {
  const { active, setActive, refresh } = useInspector();
  const [ending, setEnding] = useState(false);

  if (!active) return null;

  // Both modes are read-only this wave; break-glass (emergency, no tenant
  // consent) gets the alarming red treatment, routine consent gets amber.
  const breakGlass = active.mode === 'break_glass';

  async function end() {
    if (!active) return;
    setEnding(true);
    try {
      await endInspectorSession(active.session_id);
    } catch {
      // Even if the call fails, drop the local session so the operator is not
      // trapped behind a stale banner; refresh reconciles with the server.
    } finally {
      setActive(null);
      refresh();
      setEnding(false);
    }
  }

  return (
    <div
      role="status"
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-6 py-2 text-caption ${
        breakGlass
          ? 'border-critical/30 bg-critical-light text-critical'
          : 'border-warning/30 bg-warning-light text-warning'
      }`}
    >
      {breakGlass ? (
        <ShieldAlert size={14} className="shrink-0" aria-hidden />
      ) : (
        <Eye size={14} className="shrink-0" aria-hidden />
      )}
      <span className="font-medium">
        Inspecting{' '}
        <span className="font-mono">{active.organization_id}</span> · read-only ·{' '}
        {breakGlass ? 'break-glass' : 'consent'}
      </span>
      <span className="text-slate">
        {active.reason ? `“${active.reason}” · ` : ''}ends {relTime(active.expires_at)}
      </span>
      <button
        type="button"
        onClick={() => void end()}
        disabled={ending}
        className="ml-auto inline-flex items-center gap-1.5 rounded border border-current px-2.5 py-1 font-medium hover:bg-black/10 disabled:opacity-60"
      >
        {ending && <Loader2 size={12} className="animate-spin" aria-hidden />}
        End session
      </button>
    </div>
  );
}
