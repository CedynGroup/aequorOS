"use client";

/**
 * AequorOS Reporting Gateway — enrol this tenant's bank (channel operator only).
 *
 * The gateway is AequorOS' own reporting channel, so banks never request
 * credentials or install certificates: staff enrol them here. Enrolment creates
 * the bank's API client key and filing signature key in the platform vault, has
 * the gateway issue the client and register the key, and tests the bank's
 * connection. It is idempotent, so "Enrol again" repairs a partial enrolment
 * without issuing anything twice. Nothing is filed and nothing is signed here;
 * the signature key is used only after the bank's own preparer and approver
 * approve a return.
 */

import { useState, type FormEvent } from "react";
import { RadioTower } from "lucide-react";
import {
  ApiError,
  enrolOnReportingGateway,
  getReportingGatewayEnrolment,
  type ReportingGatewayEnrolment,
} from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { fmtDate, fmtTs, DASH } from "@/lib/format";
import {
  Button,
  Chip,
  ErrorPanel,
  Field,
  FieldRow,
  Input,
  QueryBoundary,
  SectionCard,
  SkeletonRows,
} from "@/components/ui";
import { GatewayCertificateRenewal } from "./GatewayCertificateRenewal";

const CODE = /^\d{4}$/;

function environmentLabel(
  value: ReportingGatewayEnrolment["environment"],
): string {
  return value === "production"
    ? "Production"
    : value === "sandbox"
      ? "Sandbox"
      : DASH;
}

function StatusLine({ enrolment }: { enrolment: ReportingGatewayEnrolment }) {
  if (!enrolment.enrolled) return <Chip>Not enrolled</Chip>;
  if (enrolment.last_connection_test_ok === false) {
    return <Chip tone="warn">Enrolled · connection test failed</Chip>;
  }
  return (
    <Chip tone="ok">Enrolled · {environmentLabel(enrolment.environment)}</Chip>
  );
}

export function ReportingGatewaySection({
  orgId,
  bankId,
  bankName,
}: {
  orgId: string;
  bankId: string;
  bankName: string;
}) {
  const status = useApi(
    () => getReportingGatewayEnrolment(orgId, bankId),
    [orgId, bankId],
  );
  const [code, setCode] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<ReportingGatewayEnrolment | null>(null);

  const enrolment = result ?? status.data;
  const codeInvalid = code.trim() !== "" && !CODE.test(code.trim());

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (codeInvalid) return;
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
      const enrolled = await enrolOnReportingGateway(orgId, bankId, {
        institution_code: code.trim() || null,
        contacts,
      });
      setResult(enrolled);
      status.reload();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError("enrolment_failed", "Enrolment did not complete.", 0),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <SectionCard
      title="AequorOS Reporting Gateway"
      subtitle="Enrol the bank so its approved returns can be filed through the gateway. Banks have nothing to set up."
      actions={enrolment ? <StatusLine enrolment={enrolment} /> : undefined}
    >
      <QueryBoundary
        loading={status.loading && !status.data}
        error={status.error}
        onRetry={status.reload}
        skeleton={<SkeletonRows rows={3} />}
        context="Gateway enrolment"
      >
        {enrolment && (
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="space-y-1">
              <FieldRow label="Institution code">
                <span className="font-mono">
                  {enrolment.institution_code ?? DASH}
                </span>
              </FieldRow>
              <FieldRow label="Environment">
                {environmentLabel(enrolment.environment)}
              </FieldRow>
              <FieldRow label="API client">
                <span className="font-mono">{enrolment.client_id ?? DASH}</span>
              </FieldRow>
              <FieldRow label="Client certificate">
                {enrolment.certificate_not_after
                  ? `valid until ${fmtDate(enrolment.certificate_not_after)}`
                  : DASH}
              </FieldRow>
              <FieldRow label="Signature key">
                <span className="font-mono">
                  {enrolment.signing_kid ?? DASH}
                </span>
              </FieldRow>
              <FieldRow label="Enrolled">
                {enrolment.enrolled_at ? (
                  <span title={fmtTs(enrolment.enrolled_at)}>
                    {fmtDate(enrolment.enrolled_at)}
                  </span>
                ) : (
                  DASH
                )}
              </FieldRow>
              <FieldRow label="Connection test">
                {enrolment.last_connection_test_at
                  ? `${enrolment.last_connection_test_ok ? "passed" : "failed"} · ${fmtTs(enrolment.last_connection_test_at)}`
                  : DASH}
              </FieldRow>
            </div>

            <form
              onSubmit={submit}
              className="space-y-3"
              aria-label="Enrol on the gateway"
            >
              <p className="text-caption leading-relaxed text-slate">
                {enrolment.enrolled
                  ? `${bankName} is enrolled. Enrolling again reuses its keys and client, and re-tests the connection.`
                  : `Creates ${bankName}'s secure connection and its own filing signature key, registers them on the gateway, and tests the connection. Nothing is filed.`}
              </p>
              <Field
                label="Institution code"
                htmlFor="gateway-code"
                hint="Four digits. Leave blank to use the code in the bank's institution profile."
                error={
                  codeInvalid
                    ? "Enter the four-digit institution code."
                    : undefined
                }
              >
                <Input
                  id="gateway-code"
                  inputMode="numeric"
                  placeholder={enrolment.institution_code ?? "e.g. 0142"}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  disabled={enrolment.enrolled}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Reporting contact" htmlFor="gateway-contact-name">
                  <Input
                    id="gateway-contact-name"
                    placeholder="Name and title"
                    value={contactName}
                    onChange={(e) => setContactName(e.target.value)}
                  />
                </Field>
                <Field label="Contact email" htmlFor="gateway-contact-email">
                  <Input
                    id="gateway-contact-email"
                    type="email"
                    placeholder="regreporting@bank"
                    value={contactEmail}
                    onChange={(e) => setContactEmail(e.target.value)}
                  />
                </Field>
              </div>
              <Button
                type="submit"
                loading={busy}
                disabled={codeInvalid}
                icon={<RadioTower size={14} aria-hidden />}
              >
                {enrolment.enrolled ? "Enrol again" : "Enrol on the gateway"}
              </Button>
              {result?.message && (
                <p
                  role="status"
                  className={`text-caption font-medium ${result.last_connection_test_ok ? "text-success" : "text-warning"}`}
                >
                  {result.message}
                </p>
              )}
              {error && <ErrorPanel error={error} context="Enrolment" />}
            </form>
          </div>
        )}
        {enrolment?.enrolled && (
          <div className="mt-6 border-t border-border pt-5">
            <GatewayCertificateRenewal
              orgId={orgId}
              bankId={bankId}
              enrolment={enrolment}
              onChanged={(renewed) => {
                // The renewal panel shows its own outcome; the enrolment form's
                // status line is for enrolment only.
                setResult({ ...renewed, message: null });
                status.reload();
              }}
            />
          </div>
        )}
      </QueryBoundary>
    </SectionCard>
  );
}
