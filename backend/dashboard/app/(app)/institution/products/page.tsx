'use client';

/**
 * Institution Profile — Products & licences: the product register (proposed →
 * approved → withdrawn, with the regulator approval reference) and the licence
 * register (class, issue date, active/revoked/expired). Two cards, each with
 * inline add/edit forms; every mutation records a required audit reason.
 */

import { useMemo, useState } from 'react';
import { FileBadge, Package, Pencil, Plus } from 'lucide-react';
import type {
  BankLicenseCreate,
  BankLicenseRead,
  BankProductCreate,
  BankProductRead,
  LicenseStatus,
  ProductStatus,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateBankLicense,
  useCreateBankProduct,
  useInstitutionProfile,
  useUpdateBankLicense,
  useUpdateBankProduct,
} from '@/lib/api/hooks';
import { labelize } from '@/lib/api/values';
import {
  Field,
  FormActions,
  LicenseStatusPill,
  ProductStatusPill,
  ReasonField,
  dash,
  fmtRegisterDate,
  inputCls,
  textOrNull,
} from '@/components/institution/shared';

export default function ProductsLicencesPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const query = useInstitutionProfile(bankId);
  const products = useMemo(() => query.data?.products ?? [], [query.data]);
  const licenses = useMemo(() => query.data?.licenses ?? [], [query.data]);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Institution Profile', href: '/institution' },
          { label: 'Products & licences' },
        ]}
        title="Products & licences"
        subtitle="The product approval register and the institution's operating licences"
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          skeleton={
            <div className="space-y-6">
              <div className="card">
                <SkeletonTable rows={4} />
              </div>
              <div className="card">
                <SkeletonTable rows={4} />
              </div>
            </div>
          }
        >
          <ProductsCard bankId={bankId!} products={products} />
          <LicencesCard bankId={bankId!} licenses={licenses} />
        </QueryBoundary>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------

function ProductsCard({
  bankId,
  products,
}: {
  bankId: string;
  products: BankProductRead[];
}) {
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = products.find((product) => product.id === editingId) ?? null;

  const columns: Column<BankProductRead>[] = [
    {
      key: 'name',
      header: 'Name',
      render: (product) => (
        <span className="text-body font-medium text-navy">{product.name}</span>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      render: (product) => (
        <span className="text-caption text-slate">
          {labelize(product.productType)}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (product) => <ProductStatusPill status={product.status} />,
    },
    {
      key: 'approvalRef',
      header: 'Approval ref',
      render: (product) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {dash(product.approvalReference)}
        </span>
      ),
    },
    {
      key: 'edit',
      header: '',
      align: 'right',
      render: (product) => (
        <button
          type="button"
          onClick={() => {
            setAdding(false);
            setEditingId(product.id);
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
    <SectionCard
      title="Products"
      subtitle="Product proposals and approvals with the regulator reference"
      actions={
        <button
          type="button"
          onClick={() => {
            setEditingId(null);
            setAdding(true);
          }}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
        >
          <Plus size={13} aria-hidden />
          Add product
        </button>
      }
      noPadding
    >
      {(adding || editing) && (
        <div className="px-5 pt-5 pb-4 border-b border-border-light">
          <ProductForm
            key={editing?.id ?? 'new'}
            bankId={bankId}
            product={editing}
            onClose={() => {
              setAdding(false);
              setEditingId(null);
            }}
          />
        </div>
      )}
      {products.length === 0 ? (
        <div className="p-5">
          <EmptyState
            Icon={Package}
            title="No products recorded"
            description="Track product proposals through approval or withdrawal, with the regulator's approval reference."
          />
        </div>
      ) : (
        <DataTable columns={columns} rows={products} density="compact" />
      )}
    </SectionCard>
  );
}

function ProductForm({
  bankId,
  product,
  onClose,
}: {
  bankId: string;
  product: BankProductRead | null;
  onClose: () => void;
}) {
  const create = useCreateBankProduct(bankId);
  const update = useUpdateBankProduct(bankId);
  const mutation = product ? update : create;

  const [name, setName] = useState(product?.name ?? '');
  const [productType, setProductType] = useState(product?.productType ?? '');
  const [status, setStatus] = useState<ProductStatus>(
    product?.status ?? 'proposed'
  );
  const [approvalReference, setApprovalReference] = useState(
    product?.approvalReference ?? ''
  );
  const [reason, setReason] = useState('');

  const canSubmit =
    name.trim().length > 0 &&
    productType.trim().length > 0 &&
    reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: BankProductCreate = {
      reason: reason.trim(),
      name: name.trim(),
      productType: productType.trim(),
      status,
      approvalReference: textOrNull(approvalReference),
    };
    if (product) {
      update.mutate({ productId: product.id, payload }, { onSuccess: onClose });
    } else {
      create.mutate(payload, { onSuccess: onClose });
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        {product ? `Edit ${product.name}` : 'Add product'}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Field label="Name" htmlFor="pr-name" required>
          <input
            id="pr-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Product type" htmlFor="pr-type" required>
          <input
            id="pr-type"
            value={productType}
            onChange={(e) => setProductType(e.target.value)}
            placeholder="e.g. deposit, lending, e_money"
            className={inputCls}
          />
        </Field>
        <Field label="Status" htmlFor="pr-status">
          <select
            id="pr-status"
            value={status}
            onChange={(e) => setStatus(e.target.value as ProductStatus)}
            className={inputCls}
          >
            <option value="proposed">Proposed</option>
            <option value="approved">Approved</option>
            <option value="withdrawn">Withdrawn</option>
          </select>
        </Field>
        <Field label="Approval reference" htmlFor="pr-ref">
          <input
            id="pr-ref"
            value={approvalReference}
            onChange={(e) => setApprovalReference(e.target.value)}
            className={inputCls}
          />
        </Field>
      </div>

      <div className="max-w-xl">
        <ReasonField id="pr-reason" value={reason} onChange={setReason} />
      </div>

      {mutation.error && (
        <ErrorPanel error={mutation.error} title="Could not save the product" />
      )}

      <FormActions
        submitLabel={product ? 'Save product' : 'Add product'}
        pending={mutation.isPending}
        disabled={!canSubmit}
        onCancel={onClose}
      />
    </form>
  );
}

// ---------------------------------------------------------------------------
// Licences
// ---------------------------------------------------------------------------

function LicencesCard({
  bankId,
  licenses,
}: {
  bankId: string;
  licenses: BankLicenseRead[];
}) {
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = licenses.find((license) => license.id === editingId) ?? null;

  const columns: Column<BankLicenseRead>[] = [
    {
      key: 'name',
      header: 'Licence',
      render: (license) => (
        <span className="text-body font-medium text-navy">
          {license.licenseName}
        </span>
      ),
    },
    {
      key: 'class',
      header: 'Class',
      render: (license) => (
        <span className="text-caption text-slate">
          {license.licenseClass ? labelize(license.licenseClass) : '—'}
        </span>
      ),
    },
    {
      key: 'issued',
      header: 'Issued',
      render: (license) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtRegisterDate(license.issuedOn)}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (license) => <LicenseStatusPill status={license.status} />,
    },
    {
      key: 'edit',
      header: '',
      align: 'right',
      render: (license) => (
        <button
          type="button"
          onClick={() => {
            setAdding(false);
            setEditingId(license.id);
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
    <SectionCard
      title="Licences"
      subtitle="Operating licences with class, issue date, and standing"
      actions={
        <button
          type="button"
          onClick={() => {
            setEditingId(null);
            setAdding(true);
          }}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
        >
          <Plus size={13} aria-hidden />
          Add licence
        </button>
      }
      noPadding
    >
      {(adding || editing) && (
        <div className="px-5 pt-5 pb-4 border-b border-border-light">
          <LicenseForm
            key={editing?.id ?? 'new'}
            bankId={bankId}
            license={editing}
            onClose={() => {
              setAdding(false);
              setEditingId(null);
            }}
          />
        </div>
      )}
      {licenses.length === 0 ? (
        <div className="p-5">
          <EmptyState
            Icon={FileBadge}
            title="No licences recorded"
            description="Record the institution's operating licences with class and issue date."
          />
        </div>
      ) : (
        <DataTable columns={columns} rows={licenses} density="compact" />
      )}
    </SectionCard>
  );
}

function LicenseForm({
  bankId,
  license,
  onClose,
}: {
  bankId: string;
  license: BankLicenseRead | null;
  onClose: () => void;
}) {
  const create = useCreateBankLicense(bankId);
  const update = useUpdateBankLicense(bankId);
  const mutation = license ? update : create;

  const [licenseName, setLicenseName] = useState(license?.licenseName ?? '');
  const [licenseClass, setLicenseClass] = useState(license?.licenseClass ?? '');
  const [issuedOn, setIssuedOn] = useState(license?.issuedOn ?? '');
  const [status, setStatus] = useState<LicenseStatus>(
    license?.status ?? 'active'
  );
  const [reason, setReason] = useState('');

  const canSubmit = licenseName.trim().length > 0 && reason.trim().length > 0;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const payload: BankLicenseCreate = {
      reason: reason.trim(),
      licenseName: licenseName.trim(),
      licenseClass: textOrNull(licenseClass),
      issuedOn: textOrNull(issuedOn),
      status,
    };
    if (license) {
      update.mutate({ licenseId: license.id, payload }, { onSuccess: onClose });
    } else {
      create.mutate(payload, { onSuccess: onClose });
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">
        {license ? `Edit ${license.licenseName}` : 'Add licence'}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Field label="Licence name" htmlFor="lc-name" required>
          <input
            id="lc-name"
            value={licenseName}
            onChange={(e) => setLicenseName(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Class" htmlFor="lc-class">
          <input
            id="lc-class"
            value={licenseClass}
            onChange={(e) => setLicenseClass(e.target.value)}
            placeholder="e.g. universal_banking"
            className={inputCls}
          />
        </Field>
        <Field label="Issued on" htmlFor="lc-issued">
          <input
            id="lc-issued"
            type="date"
            value={issuedOn}
            onChange={(e) => setIssuedOn(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Status" htmlFor="lc-status">
          <select
            id="lc-status"
            value={status}
            onChange={(e) => setStatus(e.target.value as LicenseStatus)}
            className={inputCls}
          >
            <option value="active">Active</option>
            <option value="revoked">Revoked</option>
            <option value="expired">Expired</option>
          </select>
        </Field>
      </div>

      <div className="max-w-xl">
        <ReasonField id="lc-reason" value={reason} onChange={setReason} />
      </div>

      {mutation.error && (
        <ErrorPanel error={mutation.error} title="Could not save the licence" />
      )}

      <FormActions
        submitLabel={license ? 'Save licence' : 'Add licence'}
        pending={mutation.isPending}
        disabled={!canSubmit}
        onCancel={onClose}
      />
    </form>
  );
}
