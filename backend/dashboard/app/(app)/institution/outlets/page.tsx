'use client';

/**
 * Institution Profile — Outlets: head office, branches, and agencies with
 * opening/closure dates. Closing an outlet is a status change on the edit
 * form (closure date + optional relocation note appear when status is
 * "closed"). Every mutation records a required audit reason.
 */

import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { MapPin, Pencil, Plus } from 'lucide-react';
import type {
  OutletCreate,
  OutletRead,
  OutletStatus,
  OutletType,
} from '@aequoros/risk-service-api';
import {
  OutletStatus as OutletStatusValues,
  OutletType as OutletTypeValues,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateOutlet,
  useInstitutionProfile,
  useUpdateOutlet,
} from '@/lib/api/hooks';
import {
  Field,
  FormActions,
  OUTLET_TYPE_LABELS,
  OutletStatusPill,
  ReasonField,
  dash,
  fmtRegisterDate,
  inputCls,
  textOrNull,
} from '@/components/institution/shared';
import CsvImport, {
  isIsoCsvDate,
  type CsvRowResult,
} from '@/components/institution/CsvImport';
import { OUTLETS_TEMPLATE } from '@/lib/templates';

// ---------------------------------------------------------------------------
// Bulk CSV import (outlets.csv) — client-side orchestration over the
// reasoned single-row createOutlet endpoint; see CsvImport for the design
// note. Enum columns are validated against the GENERATED contract values
// (OutletType / OutletStatus).
// ---------------------------------------------------------------------------

type OutletCsvPayload = Omit<OutletCreate, 'reason'>;

const OUTLET_TYPE_VALUES = Object.values(OutletTypeValues);
const OUTLET_STATUS_VALUES = Object.values(OutletStatusValues);

function parseOutletCsvRow(cells: string[]): CsvRowResult<OutletCsvPayload> {
  const [outletType, name, outletNumber, statusRaw, openedOn] = cells;
  const errors: string[] = [];
  if (!OUTLET_TYPE_VALUES.includes(outletType as OutletType)) {
    errors.push(
      `outlet_type '${outletType}' must be one of: ${OUTLET_TYPE_VALUES.join(', ')}`
    );
  }
  if (!name) errors.push('name is required');
  const status = statusRaw || 'active';
  if (!OUTLET_STATUS_VALUES.includes(status as OutletStatus)) {
    errors.push(
      `status '${statusRaw}' must be blank (= active) or one of: ${OUTLET_STATUS_VALUES.join(', ')}`
    );
  }
  if (openedOn && !isIsoCsvDate(openedOn)) {
    errors.push(`opened_on '${openedOn}' must be a valid YYYY-MM-DD date`);
  }
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    payload: {
      outletType: outletType as OutletType,
      name,
      outletNumber: outletNumber || null,
      status: status as OutletStatus,
      openedOn: openedOn || null,
      // Closures carry a closure date + audit trail — record them through
      // the edit form, not the bulk import ("closed" rows are stamped by
      // the backend when no date is given).
      closedOn: null,
      relocatedFrom: null,
      address: {},
    },
  };
}

export default function OutletsPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const queryClient = useQueryClient();

  const query = useInstitutionProfile(bankId);
  const createOutlet = useCreateOutlet(bankId);
  const outlets = useMemo(() => query.data?.outlets ?? [], [query.data]);

  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const editing = outlets.find((outlet) => outlet.id === editingId) ?? null;

  const columns: Column<OutletRead>[] = [
    {
      key: 'type',
      header: 'Type',
      render: (outlet) => (
        <span className="text-caption text-slate">
          {OUTLET_TYPE_LABELS[outlet.outletType]}
        </span>
      ),
    },
    {
      key: 'name',
      header: 'Name',
      render: (outlet) => (
        <span className="text-body font-medium text-navy">{outlet.name}</span>
      ),
    },
    {
      key: 'number',
      header: 'Number',
      render: (outlet) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {dash(outlet.outletNumber)}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (outlet) => <OutletStatusPill status={outlet.status} />,
    },
    {
      key: 'opened',
      header: 'Opened',
      render: (outlet) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtRegisterDate(outlet.openedOn)}
        </span>
      ),
    },
    {
      key: 'closed',
      header: 'Closed',
      render: (outlet) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtRegisterDate(outlet.closedOn)}
        </span>
      ),
    },
    {
      key: 'edit',
      header: '',
      align: 'right',
      render: (outlet) => (
        <button
          type="button"
          onClick={() => {
            setAdding(false);
            setEditingId(outlet.id);
          }}
          className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-micro font-medium text-slate hover:text-navy hover:border-slate"
        >
          <Pencil size={11} aria-hidden />
          Edit
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Institution Profile', href: '/institution' },
          { label: 'Outlets' },
        ]}
        title="Outlets"
        subtitle="Head office, branches, and agencies — openings, closures, relocations"
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
            Add outlet
          </button>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <CsvImport<OutletCsvPayload>
          entityLabel="outlets"
          template={OUTLETS_TEMPLATE}
          disabled={!bankId}
          parseRow={parseOutletCsvRow}
          importRow={(payload, reason) =>
            createOutlet.mutateAsync({ ...payload, reason })
          }
          onFinished={() =>
            void queryClient.invalidateQueries({
              queryKey: ['institution-profile', bankId],
            })
          }
        />

        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          skeleton={
            <div className="card">
              <SkeletonTable rows={5} />
            </div>
          }
        >
          {(adding || editing) && (
            <OutletForm
              key={editing?.id ?? 'new'}
              bankId={bankId!}
              outlet={editing}
              onClose={() => {
                setAdding(false);
                setEditingId(null);
              }}
            />
          )}

          <SectionCard
            title="Outlet register"
            subtitle={`${outlets.length} ${outlets.length === 1 ? 'outlet' : 'outlets'} on record`}
            noPadding
          >
            {outlets.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={MapPin}
                  title="No outlets recorded"
                  description="Add the head office, branches, and agencies to complete the outlet register."
                />
              </div>
            ) : (
              <DataTable columns={columns} rows={outlets} density="compact" />
            )}
          </SectionCard>
        </QueryBoundary>
      </div>
    </>
  );
}

function OutletForm({
  bankId,
  outlet,
  onClose,
}: {
  bankId: string;
  outlet: OutletRead | null;
  onClose: () => void;
}) {
  const create = useCreateOutlet(bankId);
  const update = useUpdateOutlet(bankId);
  const mutation = outlet ? update : create;

  const [outletType, setOutletType] = useState<OutletType>(
    outlet?.outletType ?? 'branch'
  );
  const [name, setName] = useState(outlet?.name ?? '');
  const [outletNumber, setOutletNumber] = useState(outlet?.outletNumber ?? '');
  const [status, setStatus] = useState<OutletStatus>(outlet?.status ?? 'active');
  const [openedOn, setOpenedOn] = useState(outlet?.openedOn ?? '');
  const [closedOn, setClosedOn] = useState(outlet?.closedOn ?? '');
  const [relocatedFrom, setRelocatedFrom] = useState(
    outlet?.relocatedFrom ?? ''
  );
  const [reason, setReason] = useState('');

  const canSubmit = name.trim().length > 0 && reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: OutletCreate = {
      reason: reason.trim(),
      outletType,
      name: name.trim(),
      outletNumber: textOrNull(outletNumber),
      status,
      openedOn: textOrNull(openedOn),
      closedOn: status === 'closed' ? textOrNull(closedOn) : null,
      relocatedFrom: textOrNull(relocatedFrom),
      // Structured address details are carried through unchanged (no editor yet).
      address: outlet?.address ?? {},
    };
    if (outlet) {
      update.mutate({ outletId: outlet.id, payload }, { onSuccess: onClose });
    } else {
      create.mutate(payload, { onSuccess: onClose });
    }
  };

  return (
    <SectionCard
      title={outlet ? `Edit ${outlet.name}` : 'Add outlet'}
      subtitle={
        outlet
          ? 'Full replacement — set status to "Closed" to record a closure'
          : 'Register a new head office, branch, or agency'
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="Outlet type" htmlFor="ol-type">
            <select
              id="ol-type"
              value={outletType}
              onChange={(e) => setOutletType(e.target.value as OutletType)}
              className={inputCls}
            >
              {(Object.keys(OUTLET_TYPE_LABELS) as OutletType[]).map((value) => (
                <option key={value} value={value}>
                  {OUTLET_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Name" htmlFor="ol-name" required>
            <input
              id="ol-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Outlet number" htmlFor="ol-number">
            <input
              id="ol-number"
              value={outletNumber}
              onChange={(e) => setOutletNumber(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Status" htmlFor="ol-status">
            <select
              id="ol-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as OutletStatus)}
              className={inputCls}
            >
              <option value="active">Active</option>
              <option value="closed">Closed</option>
            </select>
          </Field>
          <Field label="Opened on" htmlFor="ol-opened">
            <input
              id="ol-opened"
              type="date"
              value={openedOn}
              onChange={(e) => setOpenedOn(e.target.value)}
              className={inputCls}
            />
          </Field>
          {status === 'closed' && (
            <Field
              label="Closed on"
              htmlFor="ol-closed"
              hint="Left blank, the closure is stamped with today's date."
            >
              <input
                id="ol-closed"
                type="date"
                value={closedOn}
                onChange={(e) => setClosedOn(e.target.value)}
                className={inputCls}
              />
            </Field>
          )}
          <Field
            label="Relocated from"
            htmlFor="ol-relocated"
            className="sm:col-span-2"
          >
            <input
              id="ol-relocated"
              value={relocatedFrom}
              onChange={(e) => setRelocatedFrom(e.target.value)}
              placeholder="Previous location, if this outlet relocated"
              className={inputCls}
            />
          </Field>
        </div>

        <div className="max-w-xl">
          <ReasonField id="ol-reason" value={reason} onChange={setReason} />
        </div>

        {mutation.error && (
          <ErrorPanel error={mutation.error} title="Could not save the outlet" />
        )}

        <FormActions
          submitLabel={outlet ? 'Save outlet' : 'Add outlet'}
          pending={mutation.isPending}
          disabled={!canSubmit}
          onCancel={onClose}
        />
      </form>
    </SectionCard>
  );
}
