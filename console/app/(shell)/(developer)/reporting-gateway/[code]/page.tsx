"use client";

/**
 * /reporting-gateway/[code] — one institution on the AequorOS Reporting Gateway.
 *
 * For an institution filing with its own software: issue its API client from the
 * certificate request it sends, register its filing signature key (public key
 * only), mark it live or suspend it, revoke a client, retire a key. The
 * institution keeps its private keys; nothing here ever asks for one.
 *
 * An AequorOS client bank shows the same record, but its client and signature
 * key are managed from its tenant page, where AequorOS holds them.
 */

import { use, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  ApiError,
  changeGatewayInstitutionStatus,
  getGatewayInstitution,
  issueGatewayClient,
  registerGatewaySigningKey,
  retireGatewaySigningKey,
  revokeGatewayClient,
  type GatewayClientIssued,
  type GatewayInstitution,
} from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { fmtDate } from "@/lib/format";
import {
  Button,
  Chip,
  CopyButton,
  ErrorPanel,
  Field,
  FieldRow,
  Input,
  PageHeader,
  QueryBoundary,
  SectionCard,
  Select,
  SkeletonRows,
  Textarea,
} from "@/components/ui";
import {
  ENVIRONMENT_LABEL,
  STATUS_LABEL,
  STATUS_TONE,
  institutionTypeLabel,
  parsePublicJwk,
} from "@/components/gateway/labels";

type Act = (fn: () => Promise<GatewayInstitution>) => Promise<boolean>;

function asError(err: unknown, fallback: string): ApiError {
  return err instanceof ApiError ? err : new ApiError("gateway_action_failed", fallback, 0);
}

function IssueClient({ code, onIssued }: { code: string; onIssued: () => void }) {
  const [environment, setEnvironment] = useState<"sandbox" | "production">("sandbox");
  const [csr, setCsr] = useState("");
  const [jwkText, setJwkText] = useState("");
  const [software, setSoftware] = useState("");
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [issued, setIssued] = useState<GatewayClientIssued | null>(null);
  const jwk = jwkText.trim() ? parsePublicJwk(jwkText) : null;
  const jwkError = typeof jwk === "string" ? jwk : undefined;
  const csrError =
    csr.trim() && !csr.includes("BEGIN CERTIFICATE REQUEST")
      ? "Paste the certificate request (PEM, BEGIN CERTIFICATE REQUEST)."
      : undefined;
  const ready = csr.trim() !== "" && !csrError && jwk !== null && typeof jwk !== "string";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || typeof jwk === "string" || jwk === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await issueGatewayClient(code, {
        environment,
        csr_pem: csr.trim(),
        public_jwk: jwk,
        software: software.trim() && version.trim() ? { name: software.trim(), version: version.trim() } : null,
      });
      setIssued(result);
      onIssued();
    } catch (err) {
      setError(asError(err, "The client was not issued."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Issue an API client">
      <p className="text-caption leading-relaxed text-slate">
        The institution creates a P-256 key pair, keeps the private key, and sends you a
        certificate request for it with the same key&rsquo;s public JWK. The gateway checks the two
        match, signs the certificate, and you send the certificate back.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Environment" htmlFor="ic-env">
          <Select
            id="ic-env"
            value={environment}
            onChange={(e) => setEnvironment(e.target.value as "sandbox" | "production")}
          >
            <option value="sandbox">Sandbox (certification)</option>
            <option value="production">Production</option>
          </Select>
        </Field>
        <Field label="Software (optional)" htmlFor="ic-software">
          <Input id="ic-software" placeholder="e.g. Core reporting suite" value={software} onChange={(e) => setSoftware(e.target.value)} />
        </Field>
        <Field label="Version" htmlFor="ic-version">
          <Input id="ic-version" placeholder="e.g. 4.2" value={version} onChange={(e) => setVersion(e.target.value)} />
        </Field>
      </div>
      <Field label="Certificate request" htmlFor="ic-csr" error={csrError}>
        <Textarea id="ic-csr" rows={5} className="font-mono text-micro" value={csr} onChange={(e) => setCsr(e.target.value)} />
      </Field>
      <Field label="Public key (JWK)" htmlFor="ic-jwk" error={jwkError}>
        <Textarea id="ic-jwk" rows={3} className="font-mono text-micro" value={jwkText} onChange={(e) => setJwkText(e.target.value)} />
      </Field>
      <Button type="submit" loading={busy} disabled={!ready}>
        Issue API client
      </Button>
      {error && <ErrorPanel error={error} context="Issue client" />}
      {issued && (
        <div className="space-y-2 rounded-sm border border-border-light p-3" role="status">
          <p className="text-caption font-medium text-success">
            Client {issued.client_id} issued, valid until {fmtDate(issued.not_after)}. Send the
            client id and certificate to the institution.
          </p>
          <FieldRow label="Client id">
            <span className="font-mono">{issued.client_id}</span> <CopyButton value={issued.client_id} />
          </FieldRow>
          <FieldRow label="Certificate">
            <CopyButton value={issued.certificate_pem} label="Copy certificate" />
          </FieldRow>
          <FieldRow label="Certificate chain">
            <CopyButton value={issued.certificate_chain_pem} label="Copy chain" />
          </FieldRow>
        </div>
      )}
    </form>
  );
}

function RegisterKey({ code, act }: { code: string; act: Act }) {
  const [kid, setKid] = useState("");
  const [jwkText, setJwkText] = useState("");
  const jwk = jwkText.trim() ? parsePublicJwk(jwkText) : null;
  const jwkError = typeof jwk === "string" ? jwk : undefined;
  const kidError = kid.trim() && !/^[A-Za-z0-9._-]{3,80}$/.test(kid.trim()) ? "Letters, digits, dots, dashes and underscores only." : undefined;
  const ready = kid.trim() !== "" && !kidError && jwk !== null && typeof jwk !== "string";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || typeof jwk === "string" || jwk === null) return;
    const done = await act(() => registerGatewaySigningKey(code, { kid: kid.trim(), public_jwk: jwk }));
    if (done) {
      setKid("");
      setJwkText("");
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Register a signature key">
      <p className="text-caption leading-relaxed text-slate">
        The key the institution signs its filings with. Registering a new key does not retire the
        old one; filings signed with a retired key stay verifiable.
      </p>
      <Field label="Key name" htmlFor="rk-kid" hint="As the institution names it in its signatures." error={kidError}>
        <Input id="rk-kid" className="font-mono" value={kid} onChange={(e) => setKid(e.target.value)} />
      </Field>
      <Field label="Public key (JWK)" htmlFor="rk-jwk" error={jwkError}>
        <Textarea id="rk-jwk" rows={3} className="font-mono text-micro" value={jwkText} onChange={(e) => setJwkText(e.target.value)} />
      </Field>
      <Button type="submit" disabled={!ready}>
        Register signature key
      </Button>
    </form>
  );
}

function StatusControl({ institution, act }: { institution: GatewayInstitution; act: Act }) {
  const [reason, setReason] = useState("");
  const next = institution.status === "suspended" ? "live" : institution.status === "live" ? "suspended" : "live";
  const label = next === "live" ? (institution.status === "suspended" ? "Reinstate" : "Mark live") : "Suspend";
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      aria-label="Change status"
      onSubmit={async (event) => {
        event.preventDefault();
        if (reason.trim().length < 3) return;
        if (await act(() => changeGatewayInstitutionStatus(institution.code, { status: next, reason: reason.trim() }))) {
          setReason("");
        }
      }}
    >
      <Field label="Reason" htmlFor="st-reason" hint={next === "suspended" ? "A suspended institution's clients cannot obtain tokens." : "Recorded in the gateway's audit chain."}>
        <Input id="st-reason" className="w-96" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      <Button type="submit" variant={next === "suspended" ? "danger" : "primary"} disabled={reason.trim().length < 3}>
        {label}
      </Button>
    </form>
  );
}

export default function GatewayInstitutionPage({ params }: { params: Promise<{ code: string }> }) {
  const { code: raw } = use(params);
  const code = decodeURIComponent(raw);
  const record = useApi(() => getGatewayInstitution(code), [code]);
  const [current, setCurrent] = useState<GatewayInstitution | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const institution = current ?? record.data;
  const managedByAequoros = Boolean(institution?.aequoros_bank);

  const act: Act = async (fn) => {
    setError(null);
    try {
      setCurrent(await fn());
      return true;
    } catch (err) {
      setError(asError(err, "The gateway did not complete the action."));
      return false;
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={institution ? `${institution.name}` : `Institution ${code}`}
        subtitle={`AequorOS Reporting Gateway · institution ${code}`}
        breadcrumbs={[
          { label: "Reporting Gateway", href: "/reporting-gateway" },
          { label: code },
        ]}
      />
      <QueryBoundary
        loading={record.loading && !record.data}
        error={record.error}
        onRetry={record.reload}
        skeleton={<SkeletonRows rows={5} />}
        context="Gateway institution"
      >
        {institution && (
          <>
            {error && <ErrorPanel error={error} context="Gateway" />}
            <SectionCard
              title="Enrolment"
              actions={<Chip tone={STATUS_TONE[institution.status]}>{STATUS_LABEL[institution.status]}</Chip>}
            >
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="space-y-1">
                  <FieldRow label="Institution code"><span className="font-mono">{institution.code}</span></FieldRow>
                  <FieldRow label="Licence type">{institutionTypeLabel(institution.type)}</FieldRow>
                  <FieldRow label="Enrolled">{fmtDate(institution.enrolled_at)}</FieldRow>
                  <FieldRow label="Files through">
                    {institution.aequoros_bank ? (
                      <Link href={`/tenants/${encodeURIComponent(institution.aequoros_bank.organization_id)}`} className="text-action hover:underline">
                        AequorOS · {institution.aequoros_bank.name}
                      </Link>
                    ) : (
                      "Its own software"
                    )}
                  </FieldRow>
                </div>
                <StatusControl institution={institution} act={act} />
              </div>
              {managedByAequoros && (
                <p className="mt-3 text-caption text-slate">
                  An AequorOS client bank: AequorOS holds its keys. Its client and signature key are
                  managed from its tenant page.
                </p>
              )}
            </SectionCard>

            <SectionCard title="API clients" subtitle="Each client authenticates with its own certificate and key.">
              {institution.clients.length === 0 ? (
                <p className="text-caption text-slate">No client issued yet.</p>
              ) : (
                <table className="w-full text-caption">
                  <thead>
                    <tr className="border-b border-border text-left text-slate">
                      <th scope="col" className="py-2 pr-3 font-medium">Client id</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Environment</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Certificate valid until</th>
                      <th scope="col" className="py-2 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {institution.clients.map((client) => (
                      <tr key={client.client_id} className="border-b border-border-light">
                        <td className="py-2 pr-3 font-mono">{client.client_id}</td>
                        <td className="py-2 pr-3">{ENVIRONMENT_LABEL[client.environment]}</td>
                        <td className="py-2 pr-3">{fmtDate(client.certificate_not_after)}</td>
                        <td className="py-2">
                          {client.revoked ? (
                            <Chip tone="warn">Revoked</Chip>
                          ) : managedByAequoros ? (
                            <Chip tone="ok">Active</Chip>
                          ) : (
                            <span className="flex items-center gap-2">
                              <Chip tone="ok">Active</Chip>
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={revokeReason.trim().length < 3}
                                onClick={() => act(() => revokeGatewayClient(institution.code, client.client_id, revokeReason.trim()))}
                              >
                                Revoke
                              </Button>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {!managedByAequoros && institution.clients.some((c) => !c.revoked) && (
                <div className="mt-3 max-w-md">
                  <Field label="Reason for revoking" htmlFor="rv-reason" hint="Required before Revoke is available.">
                    <Input id="rv-reason" value={revokeReason} onChange={(e) => setRevokeReason(e.target.value)} />
                  </Field>
                </div>
              )}
              {!managedByAequoros && (
                <div className="mt-5 border-t border-border-light pt-4">
                  <IssueClient code={institution.code} onIssued={record.reload} />
                </div>
              )}
            </SectionCard>

            <SectionCard title="Filing signature keys" subtitle="Public keys only; the institution keeps the private key.">
              {institution.signing_keys.length === 0 ? (
                <p className="text-caption text-slate">No signature key registered yet.</p>
              ) : (
                <table className="w-full text-caption">
                  <thead>
                    <tr className="border-b border-border text-left text-slate">
                      <th scope="col" className="py-2 pr-3 font-medium">Key name</th>
                      <th scope="col" className="py-2 pr-3 font-medium">Registered</th>
                      <th scope="col" className="py-2 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {institution.signing_keys.map((key) => (
                      <tr key={key.kid} className="border-b border-border-light">
                        <td className="py-2 pr-3 font-mono">{key.kid}</td>
                        <td className="py-2 pr-3">{fmtDate(key.registered_at)}</td>
                        <td className="py-2">
                          <span className="flex items-center gap-2">
                            <Chip tone={key.status === "active" ? "ok" : "neutral"}>
                              {key.status === "active" ? "Active" : key.status === "retired" ? "Retired" : "Revoked"}
                            </Chip>
                            {key.status === "active" && !managedByAequoros && (
                              <Button size="sm" variant="secondary" onClick={() => act(() => retireGatewaySigningKey(institution.code, key.kid))}>
                                Retire
                              </Button>
                            )}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {!managedByAequoros && (
                <div className="mt-5 border-t border-border-light pt-4">
                  <RegisterKey code={institution.code} act={act} />
                </div>
              )}
            </SectionCard>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
