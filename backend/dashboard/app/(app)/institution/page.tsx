'use client';

/**
 * Institution Profile — the corporate profile card of the register: legal
 * identity, authorisation, capital, listing, and ownership split, with the
 * backend's advisory warnings surfaced as amber notes. Editing PUTs the full
 * profile with a required audit reason; the register is the data source for
 * the LRT return family (deep link to the Returns workspace).
 */

import { useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowUpRight, Building2, Pencil } from 'lucide-react';
import type {
  InstitutionProfileFullReadProfile,
  InstitutionProfilePut,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import {
  useInstitutionProfile,
  useSaveInstitutionProfile,
} from '@/lib/api/hooks';
import { fmtTimestamp, labelize, num } from '@/lib/api/values';
import { currencyCode, fmtCurrencyFull, fmtPct, submissionPortal } from '@/lib/format';
import {
  Field,
  FormActions,
  ReasonField,
  dash,
  fmtRegisterDate,
  inputCls,
  textOrNull,
} from '@/components/institution/shared';

export default function InstitutionProfilePage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const query = useInstitutionProfile(bankId);
  const [editing, setEditing] = useState(false);

  // The composed read returns profile: null until first configured; treat a
  // 404 (register endpoint unavailable for the bank) the same way.
  const notFound = isApiError(query.error) && query.error.status === 404;
  const profile = query.data?.profile ?? null;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Institution Profile' },
        ]}
        title="Institution Profile"
        subtitle="Corporate register — the master data behind the LRT return family"
        action={
          <Link
            href="/submissions/returns?code=LRT-PROFILE"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
          >
            Generate LRT packs
            <ArrowUpRight size={13} aria-hidden />
          </Link>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={query.isLoading}
          error={notFound ? null : query.error}
          onRetry={() => query.refetch()}
          skeleton={<SkeletonCard />}
        >
          {!profile && !editing ? (
            <EmptyState
              Icon={Building2}
              title="Profile not configured yet"
              description="Record the institution's legal identity, authorisation, approved capital, listing, and ownership split. The register feeds the corporate (LRT) returns."
              action={
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  <Pencil size={13} aria-hidden />
                  Set up profile
                </button>
              }
            />
          ) : editing ? (
            <ProfileForm
              bankId={bankId!}
              profile={profile}
              onClose={() => setEditing(false)}
            />
          ) : profile ? (
            <ProfileView profile={profile} onEdit={() => setEditing(true)} />
          ) : null}
        </QueryBoundary>
      </div>
    </>
  );
}

function ProfileView({
  profile,
  onEdit,
}: {
  profile: InstitutionProfileFullReadProfile;
  onEdit: () => void;
}) {
  return (
    <SectionCard
      title="Corporate profile"
      subtitle="Legal identity, authorisation, capital, listing, and ownership"
      actions={
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
        >
          <Pencil size={13} aria-hidden />
          Edit profile
        </button>
      }
      footer={
        <span>
          Last updated{' '}
          <span className="font-mono tnum">{fmtTimestamp(profile.updatedAt)}</span>
        </span>
      }
    >
      <div className="space-y-5">
        {profile.warnings.length > 0 && (
          <div className="rounded border border-warning/25 bg-warning-light/50 px-3.5 py-2.5 space-y-1.5">
            {profile.warnings.map((warning) => (
              <p
                key={warning}
                className="flex items-start gap-2 text-caption text-navy/85 leading-relaxed"
              >
                <AlertTriangle
                  size={13}
                  className="text-warning shrink-0 mt-0.5"
                  aria-hidden
                />
                {warning}
              </p>
            ))}
          </div>
        )}

        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-body">
          <ProfileItem label="Institution type" value={labelize(profile.institutionType)} />
          <ProfileItem
            label="Legal entity structure"
            value={labelize(profile.legalEntityStructure)}
          />
          <ProfileItem
            label="Authorisation date"
            value={fmtRegisterDate(profile.authorisationDate)}
            mono
          />
          <ProfileItem
            label="Incorporation date"
            value={fmtRegisterDate(profile.incorporationDate)}
            mono
          />
          <ProfileItem
            label="Approved capital"
            value={
              profile.approvedCapital != null
                ? fmtCurrencyFull(num(profile.approvedCapital))
                : '—'
            }
            mono
          />
          <ProfileItem label="TIN" value={dash(profile.tin)} mono />
          <ProfileItem
            label="Registration number"
            value={dash(profile.registrationNumber)}
            mono
          />
          <ProfileItem
            label={`${submissionPortal() ?? 'Regulator portal'} institution code`}
            value={dash(profile.orassInstitutionCode)}
            mono
          />
          <ProfileItem
            label="Exchange listing"
            value={
              profile.tradedOnExchange
                ? `${profile.exchangeName ?? 'Listed'}${
                    profile.isin ? ` · ISIN ${profile.isin}` : ''
                  }`
                : 'Not listed'
            }
          />
          <ProfileItem
            label="Local ownership"
            value={
              profile.ownershipLocalPct != null
                ? fmtPct(num(profile.ownershipLocalPct))
                : '—'
            }
            mono
          />
          <ProfileItem
            label="Foreign ownership"
            value={
              profile.ownershipForeignPct != null
                ? fmtPct(num(profile.ownershipForeignPct))
                : '—'
            }
            mono
          />
          <ProfileItem
            label="Parent country"
            value={dash(profile.parentCountryCode)}
            mono
          />
        </dl>
      </div>
    </SectionCard>
  );
}

function ProfileItem({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className={`mt-1 text-navy ${mono ? 'font-mono tnum' : ''}`}>{value}</dd>
    </div>
  );
}

function ProfileForm({
  bankId,
  profile,
  onClose,
}: {
  bankId: string;
  profile: InstitutionProfileFullReadProfile | null;
  onClose: () => void;
}) {
  const save = useSaveInstitutionProfile(bankId);

  const [institutionType, setInstitutionType] = useState(
    profile?.institutionType ?? ''
  );
  const [legalEntityStructure, setLegalEntityStructure] = useState(
    profile?.legalEntityStructure ?? ''
  );
  const [authorisationDate, setAuthorisationDate] = useState(
    profile?.authorisationDate ?? ''
  );
  const [incorporationDate, setIncorporationDate] = useState(
    profile?.incorporationDate ?? ''
  );
  const [approvedCapital, setApprovedCapital] = useState(
    profile?.approvedCapital ?? ''
  );
  const [tin, setTin] = useState(profile?.tin ?? '');
  const [registrationNumber, setRegistrationNumber] = useState(
    profile?.registrationNumber ?? ''
  );
  const [orassInstitutionCode, setOrassInstitutionCode] = useState(
    profile?.orassInstitutionCode ?? ''
  );
  const [tradedOnExchange, setTradedOnExchange] = useState(
    profile?.tradedOnExchange ?? false
  );
  const [exchangeName, setExchangeName] = useState(profile?.exchangeName ?? '');
  const [isin, setIsin] = useState(profile?.isin ?? '');
  const [ownershipLocalPct, setOwnershipLocalPct] = useState(
    profile?.ownershipLocalPct ?? ''
  );
  const [ownershipForeignPct, setOwnershipForeignPct] = useState(
    profile?.ownershipForeignPct ?? ''
  );
  const [parentCountryCode, setParentCountryCode] = useState(
    profile?.parentCountryCode ?? ''
  );
  const [reason, setReason] = useState('');

  const canSubmit =
    institutionType.trim().length > 0 &&
    legalEntityStructure.trim().length > 0 &&
    reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: InstitutionProfilePut = {
      reason: reason.trim(),
      institutionType: institutionType.trim(),
      legalEntityStructure: legalEntityStructure.trim(),
      authorisationDate: textOrNull(authorisationDate),
      incorporationDate: textOrNull(incorporationDate),
      approvedCapital: textOrNull(approvedCapital),
      tin: textOrNull(tin),
      registrationNumber: textOrNull(registrationNumber),
      orassInstitutionCode: textOrNull(orassInstitutionCode),
      tradedOnExchange,
      exchangeName: tradedOnExchange ? textOrNull(exchangeName) : null,
      isin: tradedOnExchange ? textOrNull(isin) : null,
      ownershipLocalPct: textOrNull(ownershipLocalPct),
      ownershipForeignPct: textOrNull(ownershipForeignPct),
      parentCountryCode: textOrNull(parentCountryCode),
    };
    save.mutate(payload, { onSuccess: onClose });
  };

  return (
    <SectionCard
      title={profile ? 'Edit corporate profile' : 'Set up corporate profile'}
      subtitle="Full replacement — every save records the audit reason below"
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Institution type" htmlFor="ip-type" required>
            <input
              id="ip-type"
              value={institutionType}
              onChange={(e) => setInstitutionType(e.target.value)}
              placeholder="e.g. universal_bank"
              className={inputCls}
            />
          </Field>
          <Field label="Legal entity structure" htmlFor="ip-structure" required>
            <input
              id="ip-structure"
              value={legalEntityStructure}
              onChange={(e) => setLegalEntityStructure(e.target.value)}
              placeholder="e.g. public_limited_company"
              className={inputCls}
            />
          </Field>
          <Field label="Authorisation date" htmlFor="ip-auth-date">
            <input
              id="ip-auth-date"
              type="date"
              value={authorisationDate}
              onChange={(e) => setAuthorisationDate(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Incorporation date" htmlFor="ip-inc-date">
            <input
              id="ip-inc-date"
              type="date"
              value={incorporationDate}
              onChange={(e) => setIncorporationDate(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field
            label={`Approved capital (${currencyCode()})`}
            htmlFor="ip-capital"
          >
            <input
              id="ip-capital"
              type="number"
              min="0"
              step="any"
              value={approvedCapital}
              onChange={(e) => setApprovedCapital(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="TIN" htmlFor="ip-tin">
            <input
              id="ip-tin"
              value={tin}
              onChange={(e) => setTin(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Registration number" htmlFor="ip-reg">
            <input
              id="ip-reg"
              value={registrationNumber}
              onChange={(e) => setRegistrationNumber(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field
            label={`${submissionPortal() ?? 'Regulator portal'} institution code`}
            htmlFor="ip-orass"
          >
            <input
              id="ip-orass"
              value={orassInstitutionCode}
              onChange={(e) => setOrassInstitutionCode(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Parent country code" htmlFor="ip-parent" hint="ISO country code of the parent entity, if foreign-owned.">
            <input
              id="ip-parent"
              value={parentCountryCode}
              onChange={(e) => setParentCountryCode(e.target.value)}
              placeholder="e.g. ZA"
              className={inputCls}
            />
          </Field>
          <Field label="Local ownership %" htmlFor="ip-local">
            <input
              id="ip-local"
              type="number"
              min="0"
              max="100"
              step="any"
              value={ownershipLocalPct}
              onChange={(e) => setOwnershipLocalPct(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Foreign ownership %" htmlFor="ip-foreign">
            <input
              id="ip-foreign"
              type="number"
              min="0"
              max="100"
              step="any"
              value={ownershipForeignPct}
              onChange={(e) => setOwnershipForeignPct(e.target.value)}
              className={inputCls}
            />
          </Field>
          <div className="flex flex-col justify-end gap-2">
            <label className="inline-flex items-center gap-2 text-caption text-navy">
              <input
                type="checkbox"
                checked={tradedOnExchange}
                onChange={(e) => setTradedOnExchange(e.target.checked)}
              />
              Traded on an exchange
            </label>
          </div>
          {tradedOnExchange && (
            <>
              <Field label="Exchange name" htmlFor="ip-exchange">
                <input
                  id="ip-exchange"
                  value={exchangeName}
                  onChange={(e) => setExchangeName(e.target.value)}
                  className={inputCls}
                />
              </Field>
              <Field label="ISIN" htmlFor="ip-isin">
                <input
                  id="ip-isin"
                  value={isin}
                  onChange={(e) => setIsin(e.target.value)}
                  className={inputCls}
                />
              </Field>
            </>
          )}
        </div>

        <div className="max-w-xl">
          <ReasonField id="ip-reason" value={reason} onChange={setReason} />
        </div>

        {save.error && (
          <ErrorPanel error={save.error} title="Could not save the profile" />
        )}

        <FormActions
          submitLabel="Save profile"
          pending={save.isPending}
          disabled={!canSubmit}
          onCancel={onClose}
        />
      </form>
    </SectionCard>
  );
}
