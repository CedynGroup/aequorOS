"use client";

/**
 * The bank's gateway client certificate: when it expires, when it is due for
 * renewal, what the last renewal did, and the staff action to renew it now.
 *
 * A renewal never leaves the bank unable to file: AequorOS issues a new key and
 * client, tests them as the bank, switches the bank over, and only then revokes
 * the old client. Any failure before the switch leaves the bank on its working
 * certificate. The staff plane also renews certificates on a schedule; this panel
 * says whether that check is running for this bank.
 */

import { useState, type FormEvent } from "react";
import { KeyRound, XCircle } from "lucide-react";
import {
  ApiError,
  cancelReportingGatewayCertificateRenewal,
  renewReportingGatewayCertificate,
  type GatewayCertificateRenewal,
  type ReportingGatewayEnrolment,
} from "@/lib/api";
import { fmtDate, fmtTs, DASH } from "@/lib/format";
import {
  RENEWAL_STATUS,
  certificateState,
  isCancellable,
  isUnfinished,
  scheduledCheckRunning,
} from "@/lib/gateway-renewal";
import {
  Button,
  Chip,
  ErrorPanel,
  Field,
  FieldRow,
  Textarea,
} from "@/components/ui";

function LastRenewal({ renewal }: { renewal: GatewayCertificateRenewal }) {
  const status = RENEWAL_STATUS[renewal.status];
  return (
    <div className="space-y-1 rounded-md border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={status.tone}>{status.label}</Chip>
        <span className="text-caption text-slate">
          {renewal.trigger === "scheduled"
            ? "Scheduled renewal"
            : `By ${renewal.requested_by}`}{" "}
          · {fmtTs(renewal.started_at)}
        </span>
      </div>
      <FieldRow label="Clients">
        <span className="font-mono">
          {renewal.previous_client_id ?? DASH} →{" "}
          {renewal.new_client_id ?? "not issued yet"}
        </span>
      </FieldRow>
      <FieldRow label="Reason">{renewal.reason}</FieldRow>
      {renewal.previous_revoked_at && (
        <FieldRow label="Old client revoked">
          {fmtTs(renewal.previous_revoked_at)}
        </FieldRow>
      )}
      {renewal.completed_at && renewal.status !== "revocation_pending" && (
        <FieldRow
          label={renewal.status === "cancelled" ? "Cancelled" : "Completed"}
        >
          {fmtTs(renewal.completed_at)}
        </FieldRow>
      )}
      {renewal.error && (
        <p className="text-caption leading-relaxed text-warning" role="status">
          {renewal.error}
          {renewal.failures > 1 ? ` (${renewal.failures} attempts)` : ""}
        </p>
      )}
    </div>
  );
}

export function GatewayCertificateRenewal({
  orgId,
  bankId,
  enrolment,
  onChanged,
}: {
  orgId: string;
  bankId: string;
  enrolment: ReportingGatewayEnrolment;
  onChanged: (result: ReportingGatewayEnrolment) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"renew" | "cancel" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Read once per render: the panel states facts about today, not a live clock.
  const now = Date.now();
  const renewal = enrolment.certificate_renewal ?? null;
  const state = certificateState(enrolment, now);
  const checkRunning = scheduledCheckRunning(enrolment, now);
  const reasonValid = reason.trim().length >= 3;

  async function act(kind: "renew" | "cancel") {
    if (!reasonValid) return;
    setBusy(kind);
    setError(null);
    setMessage(null);
    try {
      const result =
        kind === "renew"
          ? await renewReportingGatewayCertificate(orgId, bankId, reason.trim())
          : await cancelReportingGatewayCertificateRenewal(
              orgId,
              bankId,
              reason.trim(),
            );
      setMessage(result.message ?? null);
      setReason("");
      onChanged(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError("renewal_failed", "The renewal did not complete.", 0),
      );
    } finally {
      setBusy(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void act("renew");
  }

  return (
    <section aria-labelledby="gateway-certificate-title" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3
          id="gateway-certificate-title"
          className="text-body font-semibold text-ink"
        >
          Client certificate
        </h3>
        <Chip tone={state.tone}>{state.label}</Chip>
      </div>
      <div className="space-y-1">
        <FieldRow label="Valid until">
          {enrolment.certificate_not_after
            ? fmtDate(enrolment.certificate_not_after)
            : DASH}
        </FieldRow>
        <FieldRow label="Renewal due from">
          {enrolment.renewal_due_from
            ? fmtDate(enrolment.renewal_due_from)
            : DASH}
        </FieldRow>
        <FieldRow label="Scheduled renewal">
          {checkRunning && enrolment.last_renewal_check_at ? (
            <span title={fmtTs(enrolment.last_renewal_check_at)}>
              Running · last checked {fmtTs(enrolment.last_renewal_check_at)}
            </span>
          ) : (
            <span className="text-warning">
              Not running for this bank — renew it here before it is due
            </span>
          )}
        </FieldRow>
      </div>

      {renewal && <LastRenewal renewal={renewal} />}

      <form
        onSubmit={submit}
        className="space-y-3"
        aria-label="Renew the client certificate"
      >
        <p className="text-caption leading-relaxed text-slate">
          Renewing issues a new key and client certificate, tests them as the
          bank, switches the bank over and then revokes the old client. The bank
          keeps filing on its current certificate until the new one has passed
          its test.
        </p>
        <Field
          label="Reason"
          htmlFor="gateway-renewal-reason"
          hint="Recorded with the renewal in the bank's audit trail and the staff log."
          error={
            reason !== "" && !reasonValid
              ? "Give a reason of at least three characters."
              : undefined
          }
        >
          <Textarea
            id="gateway-renewal-reason"
            rows={2}
            placeholder="e.g. Annual renewal ahead of expiry"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button
            type="submit"
            loading={busy === "renew"}
            disabled={!reasonValid || busy !== null}
            icon={<KeyRound size={14} aria-hidden />}
          >
            {isUnfinished(renewal) ? "Resume renewal" : "Renew certificate now"}
          </Button>
          {isCancellable(renewal) && (
            <Button
              type="button"
              variant="secondary"
              loading={busy === "cancel"}
              disabled={!reasonValid || busy !== null}
              icon={<XCircle size={14} aria-hidden />}
              onClick={() => void act("cancel")}
            >
              Cancel renewal
            </Button>
          )}
        </div>
        {message && (
          <p role="status" className="text-caption font-medium text-success">
            {message}
          </p>
        )}
        {error && <ErrorPanel error={error} context="Certificate renewal" />}
      </form>
    </section>
  );
}
