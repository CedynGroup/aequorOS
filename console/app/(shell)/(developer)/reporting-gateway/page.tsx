"use client";

/**
 * /reporting-gateway — every institution on the AequorOS Reporting Gateway.
 *
 * AequorOS client banks are enrolled from their tenant page, where AequorOS
 * creates and holds their keys. An institution that files with its own software
 * is enrolled here; it then sends its certificate request and public keys, which
 * staff relay on its page. Source: GET/POST /operator/v1/reporting-gateway/institutions.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { RadioTower } from "lucide-react";
import {
  ApiError,
  enrolGatewayInstitution,
  listGatewayInstitutions,
  type GatewayInstitutionType,
} from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { fmtDate } from "@/lib/format";
import {
  Button,
  Chip,
  EmptyState,
  ErrorPanel,
  Field,
  Input,
  PageHeader,
  QueryBoundary,
  SectionCard,
  Select,
  SkeletonRows,
} from "@/components/ui";
import {
  ENVIRONMENT_LABEL,
  INSTITUTION_TYPES,
  STATUS_LABEL,
  STATUS_TONE,
  institutionTypeLabel,
} from "@/components/gateway/labels";

const CODE = /^\d{4}$/;

function EnrolForm() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<GatewayInstitutionType>("rural_bank");
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const codeInvalid = code.trim() !== "" && !CODE.test(code.trim());
  const ready = CODE.test(code.trim()) && name.trim().length >= 2;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const contacts =
        contactName.trim() && contactEmail.trim()
          ? [
              {
                role: "Regulatory reporting contact",
                name: contactName.trim(),
                email: contactEmail.trim(),
              },
            ]
          : [];
      const enrolled = await enrolGatewayInstitution({
        code: code.trim(),
        name: name.trim(),
        type,
        contacts,
      });
      router.push(`/reporting-gateway/${encodeURIComponent(enrolled.code)}`);
    } catch (err) {
      setError(
        err instanceof ApiError ? err : new ApiError("enrolment_failed", "Enrolment did not complete.", 0),
      );
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Enrol an institution">
      <p className="text-caption leading-relaxed text-slate">
        For an institution that files with its own software. It generates its own keys: once
        enrolled, it sends you its certificate request and public keys, which you add on its page.
        An AequorOS client bank is enrolled from its tenant page instead.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          label="Institution code"
          htmlFor="gi-code"
          hint="Four digits, as the regulator assigns it."
          error={codeInvalid ? "Enter the four-digit institution code." : undefined}
        >
          <Input
            id="gi-code"
            inputMode="numeric"
            placeholder="e.g. 0301"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
        <Field label="Institution name" htmlFor="gi-name">
          <Input id="gi-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Licence type" htmlFor="gi-type">
          <Select
            id="gi-type"
            value={type}
            onChange={(e) => setType(e.target.value as GatewayInstitutionType)}
          >
            {INSTITUTION_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Reporting contact" htmlFor="gi-contact">
          <Input
            id="gi-contact"
            placeholder="Name and title"
            value={contactName}
            onChange={(e) => setContactName(e.target.value)}
          />
        </Field>
        <Field label="Contact email" htmlFor="gi-email">
          <Input
            id="gi-email"
            type="email"
            value={contactEmail}
            onChange={(e) => setContactEmail(e.target.value)}
          />
        </Field>
      </div>
      <Button type="submit" loading={busy} disabled={!ready} icon={<RadioTower size={14} aria-hidden />}>
        Enrol institution
      </Button>
      {error && <ErrorPanel error={error} context="Enrolment" />}
    </form>
  );
}

export default function ReportingGatewayPage() {
  const list = useApi(listGatewayInstitutions);
  const items = list.data?.items ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AequorOS Reporting Gateway"
        subtitle="Every institution on the channel: AequorOS client banks and institutions filing with their own software."
      />

      <SectionCard
        title="Institutions"
        subtitle="Banks enrolled from a tenant page file through AequorOS, which holds their keys."
      >
        <QueryBoundary
          loading={list.loading && !list.data}
          error={list.error}
          onRetry={list.reload}
          skeleton={<SkeletonRows rows={4} />}
          context="Gateway institutions"
        >
          {items.length === 0 ? (
            <EmptyState
              title="No institutions yet"
              hint="Enrol one below, or enrol an AequorOS client bank from its tenant page."
            />
          ) : (
            <table className="w-full text-caption">
              <thead>
                <tr className="border-b border-border text-left text-slate">
                  <th scope="col" className="py-2 pr-3 font-medium">Code</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Institution</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Licence type</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Files through</th>
                  <th scope="col" className="py-2 pr-3 font-medium">API clients</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Signature keys</th>
                  <th scope="col" className="py-2 font-medium">Enrolled</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const active = item.clients.filter((c) => !c.revoked);
                  const environments = Array.from(new Set(active.map((c) => ENVIRONMENT_LABEL[c.environment])));
                  return (
                    <tr key={item.code} className="border-b border-border-light">
                      <td className="py-2 pr-3 font-mono">
                        <Link href={`/reporting-gateway/${encodeURIComponent(item.code)}`} className="text-action hover:underline">
                          {item.code}
                        </Link>
                      </td>
                      <td className="py-2 pr-3 text-navy">{item.name}</td>
                      <td className="py-2 pr-3">{institutionTypeLabel(item.type)}</td>
                      <td className="py-2 pr-3">
                        <Chip tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Chip>
                      </td>
                      <td className="py-2 pr-3">
                        {item.aequoros_bank ? (
                          <Link
                            href={`/tenants/${encodeURIComponent(item.aequoros_bank.organization_id)}`}
                            className="text-action hover:underline"
                          >
                            AequorOS · {item.aequoros_bank.name}
                          </Link>
                        ) : (
                          "Its own software"
                        )}
                      </td>
                      <td className="py-2 pr-3">
                        {active.length === 0 ? "None" : `${active.length} · ${environments.join(", ")}`}
                      </td>
                      <td className="py-2 pr-3">
                        {item.signing_keys.filter((k) => k.status === "active").length || "None"}
                      </td>
                      <td className="py-2">{fmtDate(item.enrolled_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </QueryBoundary>
      </SectionCard>

      <SectionCard title="Enrol an institution" subtitle="An institution that files with its own software.">
        <EnrolForm />
      </SectionCard>
    </div>
  );
}
