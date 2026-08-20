'use client';

/**
 * Governed macro-scenario library (docs/stress.md §4 item 2). Lists the versioned
 * scenario library with the maker-checker status visible, filterable by type /
 * severity / status, supports N-scenario selection for the workbench, and drives
 * the lifecycle transitions (submit → approve → archive) plus edit of drafts.
 */

import { type ReactNode, useMemo, useState } from 'react';
import { CheckCircle2, Pencil, Plus, Send, Archive } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { ApiError } from '@/lib/api/client';
import { fmtDateUTC } from '@/lib/api/values';
import {
  useApproveMacroScenario,
  useArchiveMacroScenario,
  useMacroScenarios,
  useSubmitMacroScenario,
} from './hooks';
import { SCENARIO_STATUSES, SCENARIO_TYPES, type ScenarioStatus } from './macro';
import type { MacroScenarioSummary } from './types';

const STATUS_TONE: Record<string, StatusTone> = {
  draft: 'slate',
  pending_approval: 'amber',
  approved: 'success',
  archived: 'pending',
};

const SEVERITY_TONE: Record<string, StatusTone> = {
  mild: 'slate',
  moderate: 'amber',
  severe: 'critical',
};

export default function ScenarioLibrary({
  selectedIds,
  onToggleSelect,
  onEdit,
  onNew,
}: {
  selectedIds: string[];
  onToggleSelect: (scenario: MacroScenarioSummary) => void;
  onEdit: (scenarioId: string) => void;
  onNew: () => void;
}) {
  const [statusFilter, setStatusFilter] = useState<ScenarioStatus | ''>('');
  const [typeFilter, setTypeFilter] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const scenarios = useMacroScenarios({
    status: statusFilter || undefined,
    scenarioType: (typeFilter || undefined) as never,
    includeArchived: statusFilter === 'archived',
  });

  const submit = useSubmitMacroScenario();
  const approve = useApproveMacroScenario();
  const archive = useArchiveMacroScenario();

  const rows = useMemo(() => scenarios.data?.scenarios ?? [], [scenarios.data]);

  const runTransition = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof ApiError ? `${e.errorCode ?? e.code ?? 'error'}: ${e.message}` : 'Action failed.');
    }
  };

  return (
    <SectionCard
      title="Scenario library"
      subtitle="Governed, versioned macro scenarios — maker-checker status visible; select to run or compare"
      noPadding
      actions={
        <button
          type="button"
          onClick={onNew}
          className="inline-flex items-center gap-1.5 rounded-md border border-border-light px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface"
        >
          <Plus size={13} /> New scenario
        </button>
      }
      footer={
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-caption text-slate">{selectedIds.length} selected</span>
          <div className="flex items-center gap-2 ml-auto">
            <select
              className="rounded-md border border-border-light bg-transparent px-2 py-1 text-caption text-navy"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as ScenarioStatus | '')}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {SCENARIO_STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select
              className="rounded-md border border-border-light bg-transparent px-2 py-1 text-caption text-navy"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              aria-label="Filter by type"
            >
              <option value="">All types</option>
              {SCENARIO_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
        </div>
      }
    >
      <QueryBoundary isLoading={scenarios.isLoading} error={scenarios.error} onRetry={() => scenarios.refetch()}>
        {actionError && <p className="px-5 pt-3 text-caption text-critical">{actionError}</p>}
        {rows.length === 0 ? (
          <p className="px-5 py-8 text-center text-caption text-slate">
            No scenarios yet. Author one with the builder — a scenario is a set of macro-variable paths over ≥3 years.
          </p>
        ) : (
          <ul className="divide-y divide-border-light">
            {rows.map((s) => {
              const selected = selectedIds.includes(s.id);
              const isApproved = s.status === 'approved';
              return (
                <li key={s.id} className="px-5 py-3 flex items-center gap-3">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-action"
                    checked={selected}
                    disabled={!isApproved}
                    onChange={() => onToggleSelect(s)}
                    aria-label={`Select ${s.name}`}
                    title={isApproved ? 'Select for run / comparison' : 'Only approved scenarios can be run'}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-body font-medium text-navy truncate">{s.name}</span>
                      <StatusPill tone={STATUS_TONE[s.status] ?? 'slate'}>{s.status.replace('_', ' ')}</StatusPill>
                      <StatusPill tone="action">{s.scenario_type}</StatusPill>
                      {s.severity && <StatusPill tone={SEVERITY_TONE[s.severity] ?? 'slate'}>{s.severity}</StatusPill>}
                      {s.bank_id === null && <span className="text-micro text-slate-light">org-wide</span>}
                    </div>
                    <p className="text-micro text-slate">
                      {s.code} · v{s.version} · {s.path_count} paths · {s.horizon_years}y · updated {fmtDateUTC(new Date(s.updated_at))}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {s.status === 'draft' && (
                      <>
                        <IconBtn title="Edit draft" onClick={() => onEdit(s.id)}><Pencil size={14} /></IconBtn>
                        <IconBtn
                          title="Submit for approval"
                          onClick={() => void runTransition(() => submit.mutateAsync({ scenarioId: s.id, reason: 'Submitted for approval' }))}
                        >
                          <Send size={14} />
                        </IconBtn>
                      </>
                    )}
                    {s.status === 'pending_approval' && (
                      <IconBtn
                        title="Approve"
                        tone="success"
                        onClick={() => void runTransition(() => approve.mutateAsync({ scenarioId: s.id, reason: 'Approved' }))}
                      >
                        <CheckCircle2 size={14} />
                      </IconBtn>
                    )}
                    {s.status !== 'archived' && (
                      <IconBtn
                        title="Archive"
                        onClick={() => void runTransition(() => archive.mutateAsync({ scenarioId: s.id, reason: 'Archived' }))}
                      >
                        <Archive size={14} />
                      </IconBtn>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </QueryBoundary>
    </SectionCard>
  );
}

function IconBtn({
  children,
  title,
  onClick,
  tone,
}: {
  children: ReactNode;
  title: string;
  onClick: () => void;
  tone?: 'success';
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className={`inline-flex items-center justify-center h-7 w-7 rounded-md border border-border-light hover:bg-surface ${
        tone === 'success' ? 'text-success' : 'text-slate'
      }`}
    >
      {children}
    </button>
  );
}
