'use client';

/**
 * Institution Profile — Name history: a timeline of the institution's prior
 * legal names with the change date and the recorded reason for each rename.
 * Entries can be corrected in place; every write records an audit reason.
 */

import { useMemo, useState } from 'react';
import { History, Pencil, Plus } from 'lucide-react';
import type {
  BankNameHistoryCreate,
  BankNameHistoryRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateNameHistoryEntry,
  useInstitutionProfile,
  useUpdateNameHistoryEntry,
} from '@/lib/api/hooks';
import {
  Field,
  FormActions,
  ReasonField,
  fmtRegisterDate,
  inputCls,
  textOrNull,
} from '@/components/institution/shared';

export default function NameHistoryPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const query = useInstitutionProfile(bankId);
  const entries = useMemo(() => {
    const rows = query.data?.nameHistory ?? [];
    // Timeline reads newest change first; undated entries sink to the end.
    return [...rows].sort((a, b) =>
      (b.changedOn ?? '').localeCompare(a.changedOn ?? '')
    );
  }, [query.data]);

  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = entries.find((entry) => entry.id === editingId) ?? null;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Institution Profile', href: '/institution' },
          { label: 'Name history' },
        ]}
        title="Name history"
        subtitle="Prior legal names, when they changed, and why"
        action={
          <button
            type="button"
            onClick={() => {
              setEditingId(null);
              setAdding(true);
            }}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            <Plus size={13} aria-hidden />
            Add entry
          </button>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          skeleton={<SkeletonCard />}
        >
          {(adding || editing) && (
            <NameHistoryForm
              key={editing?.id ?? 'new'}
              bankId={bankId!}
              entry={editing}
              onClose={() => {
                setAdding(false);
                setEditingId(null);
              }}
            />
          )}

          <SectionCard
            title="Timeline"
            subtitle={
              bank
                ? `${entries.length} prior ${
                    entries.length === 1 ? 'name' : 'names'
                  } on record — current legal name: ${bank.name}`
                : undefined
            }
          >
            {entries.length === 0 ? (
              <EmptyState
                Icon={History}
                title="No name changes recorded"
                description="When the institution has operated under a previous legal name, record it here with the change date and reason."
              />
            ) : (
              <ol className="space-y-0">
                {entries.map((entry, index) => (
                  <li key={entry.id} className="relative pl-6 pb-5 last:pb-0">
                    {index < entries.length - 1 && (
                      <span
                        aria-hidden
                        className="absolute left-[7px] top-4 bottom-0 w-px bg-border-light"
                      />
                    )}
                    <span
                      aria-hidden
                      className="absolute left-0 top-1 inline-flex items-center justify-center w-[15px] h-[15px] rounded-full border border-border bg-surface text-slate"
                    >
                      <History size={8} />
                    </span>
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-body font-medium text-navy">
                        {entry.previousName}
                      </p>
                      <span className="font-mono text-caption text-slate tnum">
                        changed {fmtRegisterDate(entry.changedOn)}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          setAdding(false);
                          setEditingId(entry.id);
                        }}
                        className="ml-auto inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-micro font-medium text-slate hover:text-navy hover:border-slate"
                      >
                        <Pencil size={11} aria-hidden />
                        Edit
                      </button>
                    </div>
                    {entry.changeReason && (
                      <p className="mt-1 text-caption text-navy/80 leading-relaxed">
                        {entry.changeReason}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </SectionCard>
        </QueryBoundary>
      </div>
    </>
  );
}

function NameHistoryForm({
  bankId,
  entry,
  onClose,
}: {
  bankId: string;
  entry: BankNameHistoryRead | null;
  onClose: () => void;
}) {
  const create = useCreateNameHistoryEntry(bankId);
  const update = useUpdateNameHistoryEntry(bankId);
  const mutation = entry ? update : create;

  const [previousName, setPreviousName] = useState(entry?.previousName ?? '');
  const [changedOn, setChangedOn] = useState(entry?.changedOn ?? '');
  const [changeReason, setChangeReason] = useState(entry?.changeReason ?? '');
  const [reason, setReason] = useState('');

  const canSubmit = previousName.trim().length > 0 && reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: BankNameHistoryCreate = {
      reason: reason.trim(),
      previousName: previousName.trim(),
      changedOn: textOrNull(changedOn),
      changeReason: textOrNull(changeReason),
    };
    if (entry) {
      update.mutate({ entryId: entry.id, payload }, { onSuccess: onClose });
    } else {
      create.mutate(payload, { onSuccess: onClose });
    }
  };

  return (
    <SectionCard
      title={entry ? 'Edit name-history entry' : 'Add name-history entry'}
      subtitle="The name-change reason is stored on the record; the audit reason below covers this edit"
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Previous name" htmlFor="nh-name" required>
            <input
              id="nh-name"
              value={previousName}
              onChange={(e) => setPreviousName(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Changed on" htmlFor="nh-date">
            <input
              id="nh-date"
              type="date"
              value={changedOn}
              onChange={(e) => setChangedOn(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Name change reason" htmlFor="nh-change-reason">
            <input
              id="nh-change-reason"
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
              placeholder="e.g. Rebrand after merger"
              className={inputCls}
            />
          </Field>
        </div>

        <div className="max-w-xl">
          <ReasonField id="nh-reason" value={reason} onChange={setReason} />
        </div>

        {mutation.error && (
          <ErrorPanel error={mutation.error} title="Could not save the entry" />
        )}

        <FormActions
          submitLabel={entry ? 'Save entry' : 'Add entry'}
          pending={mutation.isPending}
          disabled={!canSubmit}
          onCancel={onClose}
        />
      </form>
    </SectionCard>
  );
}
