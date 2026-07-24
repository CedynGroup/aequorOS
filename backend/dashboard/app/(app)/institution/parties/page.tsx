'use client';

/**
 * Institution Profile — Related parties: directors, key management, external
 * auditors, shareholders, UBOs, and outsourced providers, each carrying a
 * replace-on-write roles list and (for shareholders) shareholding rows with
 * optional UBO linkage. Selecting a row expands the full detail; every
 * mutation records a required audit reason.
 */

import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, Users, Pencil, X } from 'lucide-react';
import type {
  PartyStatus,
  PartyType,
  RelatedPartyCreate,
  RelatedPartyRead,
  RelatedPartyRoleCode,
  RelatedPartyRoleInput,
  ShareholderRights,
  ShareholdingCreate,
  ShareholdingRead,
} from '@aequoros/risk-service-api';
import {
  PartyType as PartyTypeValues,
  RelatedPartyRoleCode as RoleCodeValues,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateRelatedParty,
  useCreateShareholding,
  useInstitutionProfile,
  useUpdateRelatedParty,
  useUpdateShareholding,
} from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrencyFull, fmtInt, fmtPct } from '@/lib/format';
import {
  Field,
  FormActions,
  PARTY_TYPE_LABELS,
  PartyStatusPill,
  ROLE_OPTIONS,
  ReasonField,
  fmtRegisterDate,
  inputCls,
  roleLabel,
  textOrNull,
} from '@/components/institution/shared';
import CsvImport, {
  isIsoCsvDate,
  type CsvRowResult,
} from '@/components/institution/CsvImport';
import { RELATED_PARTIES_TEMPLATE } from '@/lib/templates';

// ---------------------------------------------------------------------------
// Bulk CSV import (related_parties.csv) — client-side orchestration over the
// reasoned single-row createRelatedParty endpoint; see CsvImport for the
// design note. Enum columns are validated against the GENERATED contract
// values (PartyType / RelatedPartyRoleCode), one role per CSV row.
// ---------------------------------------------------------------------------

type PartyCsvPayload = Omit<RelatedPartyCreate, 'reason'>;

const PARTY_TYPE_VALUES = Object.values(PartyTypeValues);
const ROLE_CODE_VALUES = Object.values(RoleCodeValues);

function parsePartyCsvRow(cells: string[]): CsvRowResult<PartyCsvPayload> {
  const [
    partyType,
    fullName,
    role,
    appointedOn,
    regulatedElsewhere,
    regulatedJurisdiction,
  ] = cells;
  const errors: string[] = [];
  if (!PARTY_TYPE_VALUES.includes(partyType as PartyType)) {
    errors.push(
      `party_type '${partyType}' must be one of: ${PARTY_TYPE_VALUES.join(', ')}`
    );
  }
  if (!fullName) errors.push('full_name is required');
  if (role && !ROLE_CODE_VALUES.includes(role as RelatedPartyRoleCode)) {
    errors.push(
      `role '${role}' must be blank or one of: ${ROLE_CODE_VALUES.join(', ')}`
    );
  }
  if (appointedOn && !isIsoCsvDate(appointedOn)) {
    errors.push(`appointed_on '${appointedOn}' must be a valid YYYY-MM-DD date`);
  }
  if (appointedOn && !role) errors.push('appointed_on requires a role');
  const regulatedFlag = regulatedElsewhere.toLowerCase();
  if (regulatedFlag && regulatedFlag !== 'true' && regulatedFlag !== 'false') {
    errors.push(
      `regulated_elsewhere '${regulatedElsewhere}' must be true or false (blank = false)`
    );
  }
  const isRegulated = regulatedFlag === 'true';
  if (regulatedJurisdiction && !isRegulated) {
    errors.push('regulated_jurisdiction requires regulated_elsewhere=true');
  }
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    payload: {
      fullName,
      partyType: partyType as PartyType,
      status: 'active',
      regulatedElsewhere: isRegulated,
      regulatedJurisdiction:
        isRegulated && regulatedJurisdiction ? regulatedJurisdiction : null,
      contact: {},
      roles: role
        ? [
            {
              role: role as RelatedPartyRoleCode,
              appointedOn: appointedOn || null,
            },
          ]
        : [],
    },
  };
}

export default function RelatedPartiesPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const queryClient = useQueryClient();

  const query = useInstitutionProfile(bankId);
  const createParty = useCreateRelatedParty(bankId);
  const parties = useMemo(
    () => query.data?.relatedParties ?? [],
    [query.data]
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const selected = parties.find((party) => party.id === selectedId) ?? null;

  const columns: Column<RelatedPartyRead>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (party) => (
        <span className="text-body font-medium text-navy">{party.fullName}</span>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      render: (party) => (
        <span className="text-caption text-slate">
          {PARTY_TYPE_LABELS[party.partyType]}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (party) => <PartyStatusPill status={party.status} />,
    },
    {
      key: 'roles',
      header: 'Roles',
      render: (party) =>
        party.roles.length === 0 ? (
          <span className="text-caption text-slate">—</span>
        ) : (
          <span className="flex items-center gap-1 flex-wrap">
            {party.roles.slice(0, 3).map((role) => (
              <span
                key={role.id}
                className="inline-flex items-center rounded border border-border px-1.5 py-0.5 text-micro text-navy/85"
              >
                {roleLabel(role.role)}
              </span>
            ))}
            {party.roles.length > 3 && (
              <span className="text-micro text-slate">
                +{party.roles.length - 3}
              </span>
            )}
          </span>
        ),
    },
    {
      key: 'shareholding',
      header: 'Shareholding',
      numeric: true,
      render: (party) => {
        const total = party.shareholdings.reduce(
          (sum, holding) => sum + num(holding.pctShareholding),
          0
        );
        return total > 0 ? fmtPct(total) : '—';
      },
    },
  ];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Institution Profile', href: '/institution' },
          { label: 'Related parties' },
        ]}
        title="Related parties"
        subtitle="Directors, key management, auditors, shareholders, UBOs, and outsourced providers"
        action={
          <button
            type="button"
            onClick={() => {
              setAdding(true);
              setSelectedId(null);
            }}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            <Plus size={13} aria-hidden />
            Add related party
          </button>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <CsvImport<PartyCsvPayload>
          entityLabel="related parties"
          template={RELATED_PARTIES_TEMPLATE}
          disabled={!bankId}
          parseRow={parsePartyCsvRow}
          importRow={(payload, reason) =>
            createParty.mutateAsync({ ...payload, reason })
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
          {adding && (
            <PartyForm
              bankId={bankId!}
              party={null}
              onClose={() => setAdding(false)}
            />
          )}

          <SectionCard
            title="Register"
            subtitle={`${parties.length} related ${
              parties.length === 1 ? 'party' : 'parties'
            } — click a row for roles and shareholdings`}
            noPadding
          >
            {parties.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={Users}
                  title="No related parties recorded"
                  description="Add directors, key management personnel, external auditors, shareholders, and beneficial owners to complete the corporate register."
                />
              </div>
            ) : (
              <DataTable
                columns={columns}
                rows={parties}
                density="compact"
                onRowClick={(party) => {
                  setAdding(false);
                  setSelectedId((prev) => (prev === party.id ? null : party.id));
                }}
                rowClassName={(party) =>
                  party.id === selected?.id ? 'bg-action-light/40' : ''
                }
              />
            )}
          </SectionCard>

          {selected && (
            <PartyDetail
              key={selected.id}
              bankId={bankId!}
              party={selected}
              parties={parties}
            />
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Expanded detail: roles, shareholdings, and the edit / add-shareholding forms.
// ---------------------------------------------------------------------------

function PartyDetail({
  bankId,
  party,
  parties,
}: {
  bankId: string;
  party: RelatedPartyRead;
  parties: RelatedPartyRead[];
}) {
  const [editing, setEditing] = useState(false);
  const [holdingFormFor, setHoldingFormFor] = useState<
    { holding: ShareholdingRead | null } | null
  >(null);

  const uboName = (uboPartyId: string | null) => {
    if (!uboPartyId) return '—';
    const ubo = parties.find((candidate) => candidate.id === uboPartyId);
    return ubo?.fullName ?? uboPartyId.slice(0, 8);
  };

  if (editing) {
    return (
      <PartyForm
        bankId={bankId}
        party={party}
        onClose={() => setEditing(false)}
      />
    );
  }

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          {party.fullName}
          <PartyStatusPill status={party.status} />
        </span>
      }
      subtitle={`${PARTY_TYPE_LABELS[party.partyType]}${
        party.regulatedElsewhere
          ? ` · regulated elsewhere${
              party.regulatedJurisdiction ? ` (${party.regulatedJurisdiction})` : ''
            }`
          : ''
      }`}
      actions={
        <>
          <button
            type="button"
            onClick={() => setHoldingFormFor({ holding: null })}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
          >
            <Plus size={13} aria-hidden />
            Add shareholding
          </button>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
          >
            <Pencil size={13} aria-hidden />
            Edit
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Roles detail */}
        <div>
          <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
            Roles
          </p>
          {party.roles.length === 0 ? (
            <p className="text-caption text-slate">No roles recorded.</p>
          ) : (
            <ul className="space-y-2.5">
              {party.roles.map((role) => (
                <li
                  key={role.id}
                  className="rounded border border-border-light bg-surface px-3 py-2 space-y-1"
                >
                  <p className="text-body font-medium text-navy">
                    {roleLabel(role.role)}
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5 text-caption text-navy/80">
                    <span>
                      Appointed{' '}
                      <span className="font-mono tnum">
                        {fmtRegisterDate(role.appointedOn)}
                      </span>
                    </span>
                    {role.termOfAppointment && (
                      <span>Term {role.termOfAppointment}</span>
                    )}
                    {role.sittingAllowance != null && (
                      <span>
                        Sitting allowance{' '}
                        <span className="font-mono tnum">
                          {fmtCurrencyFull(num(role.sittingAllowance))}
                        </span>
                      </span>
                    )}
                    {role.travelAllowance != null && (
                      <span>
                        Travel allowance{' '}
                        <span className="font-mono tnum">
                          {fmtCurrencyFull(num(role.travelAllowance))}
                        </span>
                      </span>
                    )}
                    {role.annualFees != null && (
                      <span>
                        Annual fees{' '}
                        <span className="font-mono tnum">
                          {fmtCurrencyFull(num(role.annualFees))}
                        </span>
                      </span>
                    )}
                    {role.icagRegistration && (
                      <span>ICAG registration {role.icagRegistration}</span>
                    )}
                  </div>
                  {role.otherResponsibilities && (
                    <p className="text-caption text-slate leading-relaxed">
                      {role.otherResponsibilities}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Shareholdings */}
        <div>
          <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
            Shareholdings
          </p>
          {party.shareholdings.length === 0 ? (
            <p className="text-caption text-slate">
              No shareholdings recorded for this party.
            </p>
          ) : (
            <ul className="space-y-2">
              {party.shareholdings.map((holding) => (
                <li
                  key={holding.id}
                  className="rounded border border-border-light bg-surface px-3 py-2"
                >
                  <div className="flex items-center gap-2 flex-wrap text-caption">
                    <span className="font-medium text-navy">
                      {labelize(holding.shareType)}
                      {holding.shareSubtype ? ` · ${holding.shareSubtype}` : ''}
                    </span>
                    <span className="text-slate">
                      {holding.shareholderRights === 'voting'
                        ? 'Voting'
                        : 'Non-voting'}
                    </span>
                    <button
                      type="button"
                      onClick={() => setHoldingFormFor({ holding })}
                      className="ml-auto inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-micro font-medium text-slate hover:text-navy hover:border-slate"
                    >
                      <Pencil size={11} aria-hidden />
                      Edit
                    </button>
                  </div>
                  <div className="mt-1 flex items-center gap-4 flex-wrap font-mono text-caption text-navy/85 tnum">
                    <span>{fmtInt(num(holding.numberOfShares))} shares</span>
                    <span>{fmtPct(num(holding.pctShareholding))}</span>
                    <span className="font-sans text-slate">
                      UBO {uboName(holding.uboPartyId)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {holdingFormFor && (
        <div className="mt-5 border-t border-border-light pt-5">
          <ShareholdingForm
            bankId={bankId}
            partyId={party.id}
            holding={holdingFormFor.holding}
            parties={parties}
            onClose={() => setHoldingFormFor(null)}
          />
        </div>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Party create / edit form — full replacement; roles are replace-on-write.
// ---------------------------------------------------------------------------

type RoleDraft = {
  role: RelatedPartyRoleCode;
  appointedOn: string;
  termOfAppointment: string;
  sittingAllowance: string;
  travelAllowance: string;
  annualFees: string;
  icagRegistration: string;
  otherResponsibilities: string;
};

function emptyRole(): RoleDraft {
  return {
    role: 'director',
    appointedOn: '',
    termOfAppointment: '',
    sittingAllowance: '',
    travelAllowance: '',
    annualFees: '',
    icagRegistration: '',
    otherResponsibilities: '',
  };
}

function PartyForm({
  bankId,
  party,
  onClose,
}: {
  bankId: string;
  party: RelatedPartyRead | null;
  onClose: () => void;
}) {
  const create = useCreateRelatedParty(bankId);
  const update = useUpdateRelatedParty(bankId);
  const mutation = party ? update : create;

  const [fullName, setFullName] = useState(party?.fullName ?? '');
  const [partyType, setPartyType] = useState<PartyType>(
    party?.partyType ?? 'individual'
  );
  const [status, setStatus] = useState<PartyStatus>(party?.status ?? 'active');
  const [regulatedElsewhere, setRegulatedElsewhere] = useState(
    party?.regulatedElsewhere ?? false
  );
  const [regulatedJurisdiction, setRegulatedJurisdiction] = useState(
    party?.regulatedJurisdiction ?? ''
  );
  const [roles, setRoles] = useState<RoleDraft[]>(
    party
      ? party.roles.map((role) => ({
          role: role.role,
          appointedOn: role.appointedOn ?? '',
          termOfAppointment: role.termOfAppointment ?? '',
          sittingAllowance: role.sittingAllowance ?? '',
          travelAllowance: role.travelAllowance ?? '',
          annualFees: role.annualFees ?? '',
          icagRegistration: role.icagRegistration ?? '',
          otherResponsibilities: role.otherResponsibilities ?? '',
        }))
      : []
  );
  const [reason, setReason] = useState('');

  const setRole = (index: number, patch: Partial<RoleDraft>) => {
    setRoles((prev) =>
      prev.map((draft, i) => (i === index ? { ...draft, ...patch } : draft))
    );
  };

  const canSubmit = fullName.trim().length > 0 && reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const rolePayloads: RelatedPartyRoleInput[] = roles.map((draft) => ({
      role: draft.role,
      appointedOn: textOrNull(draft.appointedOn),
      termOfAppointment: textOrNull(draft.termOfAppointment),
      sittingAllowance: textOrNull(draft.sittingAllowance),
      travelAllowance: textOrNull(draft.travelAllowance),
      annualFees: textOrNull(draft.annualFees),
      icagRegistration: textOrNull(draft.icagRegistration),
      otherResponsibilities: textOrNull(draft.otherResponsibilities),
    }));
    const payload: RelatedPartyCreate = {
      reason: reason.trim(),
      fullName: fullName.trim(),
      partyType,
      status,
      regulatedElsewhere,
      regulatedJurisdiction: regulatedElsewhere
        ? textOrNull(regulatedJurisdiction)
        : null,
      // Structured contact details are carried through unchanged (no editor yet).
      contact: party?.contact ?? {},
      roles: rolePayloads,
    };
    if (party) {
      update.mutate(
        { partyId: party.id, payload },
        { onSuccess: onClose }
      );
    } else {
      create.mutate(payload, { onSuccess: onClose });
    }
  };

  return (
    <SectionCard
      title={party ? `Edit ${party.fullName}` : 'Add related party'}
      subtitle="Full replacement — the roles list below replaces the stored roles on save"
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="Full name" htmlFor="rp-name" required>
            <input
              id="rp-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Party type" htmlFor="rp-type">
            <select
              id="rp-type"
              value={partyType}
              onChange={(e) => setPartyType(e.target.value as PartyType)}
              className={inputCls}
            >
              {(Object.keys(PARTY_TYPE_LABELS) as PartyType[]).map((value) => (
                <option key={value} value={value}>
                  {PARTY_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Status" htmlFor="rp-status">
            <select
              id="rp-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as PartyStatus)}
              className={inputCls}
            >
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </Field>
          <div className="flex flex-col justify-end gap-2">
            <label className="inline-flex items-center gap-2 text-caption text-navy">
              <input
                type="checkbox"
                checked={regulatedElsewhere}
                onChange={(e) => setRegulatedElsewhere(e.target.checked)}
              />
              Regulated elsewhere
            </label>
          </div>
          {regulatedElsewhere && (
            <Field label="Regulated jurisdiction" htmlFor="rp-jurisdiction">
              <input
                id="rp-jurisdiction"
                value={regulatedJurisdiction}
                onChange={(e) => setRegulatedJurisdiction(e.target.value)}
                className={inputCls}
              />
            </Field>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-micro font-medium text-slate uppercase tracking-wider">
              Roles
            </p>
            <button
              type="button"
              onClick={() => setRoles((prev) => [...prev, emptyRole()])}
              className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate"
            >
              <Plus size={11} aria-hidden />
              Add role
            </button>
          </div>
          {roles.length === 0 ? (
            <p className="text-caption text-slate">
              No roles yet — add at least one to describe the relationship.
            </p>
          ) : (
            <ul className="space-y-3">
              {roles.map((draft, index) => (
                <li
                  key={index}
                  className="rounded border border-border-light bg-surface px-3 py-3"
                >
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <Field label="Role" htmlFor={`rp-role-${index}`}>
                      <select
                        id={`rp-role-${index}`}
                        value={draft.role}
                        onChange={(e) =>
                          setRole(index, {
                            role: e.target.value as RelatedPartyRoleCode,
                          })
                        }
                        className={inputCls}
                      >
                        {ROLE_OPTIONS.map((value) => (
                          <option key={value} value={value}>
                            {roleLabel(value)}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Appointed on" htmlFor={`rp-appointed-${index}`}>
                      <input
                        id={`rp-appointed-${index}`}
                        type="date"
                        value={draft.appointedOn}
                        onChange={(e) =>
                          setRole(index, { appointedOn: e.target.value })
                        }
                        className={inputCls}
                      />
                    </Field>
                    <Field label="Term" htmlFor={`rp-term-${index}`}>
                      <input
                        id={`rp-term-${index}`}
                        value={draft.termOfAppointment}
                        onChange={(e) =>
                          setRole(index, { termOfAppointment: e.target.value })
                        }
                        placeholder="e.g. 3 years"
                        className={inputCls}
                      />
                    </Field>
                    <Field label="Sitting allowance" htmlFor={`rp-sitting-${index}`}>
                      <input
                        id={`rp-sitting-${index}`}
                        type="number"
                        min="0"
                        step="any"
                        value={draft.sittingAllowance}
                        onChange={(e) =>
                          setRole(index, { sittingAllowance: e.target.value })
                        }
                        className={inputCls}
                      />
                    </Field>
                    <Field label="Travel allowance" htmlFor={`rp-travel-${index}`}>
                      <input
                        id={`rp-travel-${index}`}
                        type="number"
                        min="0"
                        step="any"
                        value={draft.travelAllowance}
                        onChange={(e) =>
                          setRole(index, { travelAllowance: e.target.value })
                        }
                        className={inputCls}
                      />
                    </Field>
                    <Field label="Annual fees" htmlFor={`rp-fees-${index}`}>
                      <input
                        id={`rp-fees-${index}`}
                        type="number"
                        min="0"
                        step="any"
                        value={draft.annualFees}
                        onChange={(e) =>
                          setRole(index, { annualFees: e.target.value })
                        }
                        className={inputCls}
                      />
                    </Field>
                    {draft.role === 'external_auditor' && (
                      <Field
                        label="ICAG registration"
                        htmlFor={`rp-icag-${index}`}
                      >
                        <input
                          id={`rp-icag-${index}`}
                          value={draft.icagRegistration}
                          onChange={(e) =>
                            setRole(index, { icagRegistration: e.target.value })
                          }
                          className={inputCls}
                        />
                      </Field>
                    )}
                    <Field
                      label="Other responsibilities"
                      htmlFor={`rp-other-${index}`}
                      className="sm:col-span-2"
                    >
                      <input
                        id={`rp-other-${index}`}
                        value={draft.otherResponsibilities}
                        onChange={(e) =>
                          setRole(index, {
                            otherResponsibilities: e.target.value,
                          })
                        }
                        className={inputCls}
                      />
                    </Field>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      setRoles((prev) => prev.filter((_, i) => i !== index))
                    }
                    className="mt-2 inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-critical hover:border-critical/40"
                  >
                    <X size={11} aria-hidden />
                    Remove role
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="max-w-xl">
          <ReasonField id="rp-reason" value={reason} onChange={setReason} />
        </div>

        {mutation.error && (
          <ErrorPanel
            error={mutation.error}
            title={party ? 'Could not update the party' : 'Could not add the party'}
          />
        )}

        <FormActions
          submitLabel={party ? 'Save party' : 'Add party'}
          pending={mutation.isPending}
          disabled={!canSubmit}
          onCancel={onClose}
        />
      </form>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Shareholding create / edit form — UBO select limited to individual parties.
// ---------------------------------------------------------------------------

function ShareholdingForm({
  bankId,
  partyId,
  holding,
  parties,
  onClose,
}: {
  bankId: string;
  partyId: string;
  holding: ShareholdingRead | null;
  parties: RelatedPartyRead[];
  onClose: () => void;
}) {
  const create = useCreateShareholding(bankId);
  const update = useUpdateShareholding(bankId);
  const mutation = holding ? update : create;

  const [shareType, setShareType] = useState(holding?.shareType ?? 'ordinary');
  const [shareSubtype, setShareSubtype] = useState(holding?.shareSubtype ?? '');
  const [shareholderRights, setShareholderRights] = useState<ShareholderRights>(
    holding?.shareholderRights ?? 'voting'
  );
  const [numberOfShares, setNumberOfShares] = useState(
    holding != null ? String(holding.numberOfShares) : ''
  );
  const [pctShareholding, setPctShareholding] = useState(
    holding != null ? String(holding.pctShareholding) : ''
  );
  const [uboPartyId, setUboPartyId] = useState(holding?.uboPartyId ?? '');
  const [reason, setReason] = useState('');

  // Ultimate beneficial owners are natural persons — individuals only.
  const uboOptions = parties.filter(
    (candidate) => candidate.partyType === 'individual'
  );

  const canSubmit =
    shareType.trim().length > 0 &&
    numberOfShares.trim().length > 0 &&
    pctShareholding.trim().length > 0 &&
    reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: ShareholdingCreate = {
      reason: reason.trim(),
      shareType: shareType.trim(),
      shareSubtype: textOrNull(shareSubtype),
      shareholderRights,
      numberOfShares: numberOfShares.trim(),
      pctShareholding: pctShareholding.trim(),
      uboPartyId: textOrNull(uboPartyId),
    };
    if (holding) {
      update.mutate(
        { partyId, shareholdingId: holding.id, payload },
        { onSuccess: onClose }
      );
    } else {
      create.mutate({ partyId, payload }, { onSuccess: onClose });
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        {holding ? 'Edit shareholding' : 'Add shareholding'}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <Field label="Share type" htmlFor="sh-type" required>
          <input
            id="sh-type"
            value={shareType}
            onChange={(e) => setShareType(e.target.value)}
            placeholder="e.g. ordinary"
            className={inputCls}
          />
        </Field>
        <Field label="Share subtype" htmlFor="sh-subtype">
          <input
            id="sh-subtype"
            value={shareSubtype}
            onChange={(e) => setShareSubtype(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Rights" htmlFor="sh-rights">
          <select
            id="sh-rights"
            value={shareholderRights}
            onChange={(e) =>
              setShareholderRights(e.target.value as ShareholderRights)
            }
            className={inputCls}
          >
            <option value="voting">Voting</option>
            <option value="non_voting">Non-voting</option>
          </select>
        </Field>
        <Field label="Number of shares" htmlFor="sh-shares" required>
          <input
            id="sh-shares"
            type="number"
            min="0"
            step="any"
            value={numberOfShares}
            onChange={(e) => setNumberOfShares(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="% shareholding" htmlFor="sh-pct" required>
          <input
            id="sh-pct"
            type="number"
            min="0"
            max="100"
            step="any"
            value={pctShareholding}
            onChange={(e) => setPctShareholding(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field
          label="Ultimate beneficial owner"
          htmlFor="sh-ubo"
          hint="Individuals from this register only."
        >
          <select
            id="sh-ubo"
            value={uboPartyId}
            onChange={(e) => setUboPartyId(e.target.value)}
            className={inputCls}
          >
            <option value="">None</option>
            {uboOptions.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.fullName}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="max-w-xl">
        <ReasonField id="sh-reason" value={reason} onChange={setReason} />
      </div>

      {mutation.error && (
        <ErrorPanel
          error={mutation.error}
          title="Could not save the shareholding"
        />
      )}

      <FormActions
        submitLabel={holding ? 'Save shareholding' : 'Add shareholding'}
        pending={mutation.isPending}
        disabled={!canSubmit}
        onCancel={onClose}
      />
    </form>
  );
}
