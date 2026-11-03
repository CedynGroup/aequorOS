"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { useBankContext } from "@/components/shell/BankContext";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import { useDataPermission } from "@/lib/api/dataAccess";
import {
  useReconciliationDecision,
  useReconciliationExceptions,
} from "@/lib/api/reconciliation";
import { isApiError } from "@/lib/api/client";
import { formatDate } from "./shared";

export default function ReconciliationPanel() {
  const { bank } = useBankContext();
  const { profile } = useUserProfile();
  const register = useReconciliationExceptions(bank?.id);
  const decision = useReconciliationDecision(bank?.id);
  const create = useDataPermission(bank?.id, "create", "confidential");
  const approve = useDataPermission(bank?.id, "approve", "confidential");
  const id = useId();
  const [reason, setReason] = useState("");
  const [ceiling, setCeiling] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [label, setLabel] = useState("");
  const [revokeReason, setRevokeReason] = useState("");
  const [notice, setNotice] = useState("");
  const fieldClass =
    "block w-full mt-1 px-3 py-2 border border-border rounded-sm text-body text-navy";

  const actionable =
    approve &&
    Boolean(profile?.userId) &&
    (register.data?.exceptions ?? []).some(
      (row) =>
        row.status === "pending" &&
        !row.revokedAt &&
        row.requestedBy !== profile?.userId,
    );

  const error = decision.error
    ? isApiError(decision.error)
      ? decision.error.message
      : "The decision could not be recorded."
    : null;

  return (
    <section
      className="card p-5 space-y-4"
      aria-label="Reconciliation exceptions"
    >
      <div>
        <h2 className="text-h2 text-navy">Reconciliation exceptions</h2>
        <p className="mt-1 text-body text-slate">
          Propose a bounded, dated exception. It has no effect until a different
          signed-in officer approves it. Names are audit labels, not approval
          authority.
        </p>
      </div>
      {register.isError && (
        <ErrorPanel
          error={register.error}
          onRetry={() => void register.refetch()}
        />
      )}
      {register.isLoading && (
        <p className="text-body text-slate">Loading exception register…</p>
      )}
      {create && (
        <form
          className="space-y-3 border-t border-border-light pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!create) return;
            setNotice("");
            decision.mutate(
              {
                action: "propose",
                payload: {
                  reason,
                  maxGapFraction: ceiling,
                  effectiveFrom: new Date(`${from}T00:00:00Z`),
                  effectiveTo: to || undefined,
                },
              },
              {
                onSuccess: () => {
                  setReason("");
                  setNotice(
                    "Proposal recorded. A different officer must approve it.",
                  );
                },
              },
            );
          }}
        >
          <label
            htmlFor={`${id}-reason`}
            className="block text-caption text-slate"
          >
            Reason
            <textarea
              id={`${id}-reason`}
              required
              maxLength={2000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className={fieldClass}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label
              htmlFor={`${id}-ceiling`}
              className="text-caption text-slate"
            >
              Maximum gap fraction (0–1)
              <input
                id={`${id}-ceiling`}
                required
                type="number"
                min="0.000001"
                max="1"
                step="0.000001"
                value={ceiling}
                onChange={(e) => setCeiling(e.target.value)}
                className={fieldClass}
              />
            </label>
            <label htmlFor={`${id}-from`} className="text-caption text-slate">
              Effective from
              <input
                id={`${id}-from`}
                required
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className={fieldClass}
              />
            </label>
            <label htmlFor={`${id}-to`} className="text-caption text-slate">
              Effective to (optional)
              <input
                id={`${id}-to`}
                type="date"
                min={from || undefined}
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className={fieldClass}
              />
            </label>
          </div>
          <button
            type="submit"
            disabled={decision.isPending || !reason.trim()}
            className="btn-primary px-3 py-2 text-caption disabled:opacity-40"
          >
            Record proposal
          </button>
        </form>
      )}
      {actionable && (
        <div className="grid gap-3 sm:grid-cols-2 border-t border-border-light pt-4">
          <label htmlFor={`${id}-label`} className="text-caption text-slate">
            Your approval or rejection label
            <input
              id={`${id}-label`}
              maxLength={120}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className={fieldClass}
            />
          </label>
          <label htmlFor={`${id}-revoke`} className="text-caption text-slate">
            Reason for rejection
            <input
              id={`${id}-revoke`}
              maxLength={2000}
              value={revokeReason}
              onChange={(e) => setRevokeReason(e.target.value)}
              className={fieldClass}
            />
          </label>
        </div>
      )}
      <ul className="space-y-3">
        {(register.data?.exceptions ?? []).map((row) => {
          const self = row.requestedBy === profile?.userId;
          const closed = row.revokedAt != null;
          return (
            <li
              key={row.id}
              className="rounded-md border border-border bg-surface p-4 space-y-3"
            >
              <div className="flex flex-wrap gap-2 items-center">
                <StatusPill
                  tone={
                    closed
                      ? "pending"
                      : row.status === "pending"
                        ? "amber"
                        : "compliant"
                  }
                >
                  {closed
                    ? "Revoked"
                    : row.status === "pending"
                      ? self
                        ? "Awaiting separate approval"
                        : "Pending"
                      : "Approved"}
                </StatusPill>
                <span className="text-caption font-mono text-slate">
                  Ceiling {row.maxGapFraction} · {formatDate(row.effectiveFrom)}
                  {row.effectiveTo
                    ? ` to ${formatDate(row.effectiveTo)}`
                    : " onward"}
                </span>
              </div>
              <p className="text-body text-navy">{row.reason}</p>
              {row.approvedBy && (
                <p className="text-caption text-slate">
                  Approved by {row.approvedBy}
                </p>
              )}
              {row.revokedAt && (
                <p className="text-caption text-slate">
                  Revoked{row.revokedBy ? ` by ${row.revokedBy}` : ""} ·{" "}
                  {formatDate(row.revokedAt)}
                </p>
              )}
              {!closed && row.status === "pending" && (
                <div className="border-t border-border-light pt-3">
                  {self ? (
                    <p className="text-caption text-slate">
                      You cannot approve your own proposal.
                    </p>
                  ) : approve && profile?.userId ? (
                    <div className="flex flex-wrap gap-3">
                      <button
                        type="button"
                        disabled={!label.trim() || decision.isPending}
                        className="btn-primary px-3 py-2 text-caption disabled:opacity-40"
                        onClick={() =>
                          decision.mutate({
                            action: "approve",
                            exceptionId: row.id,
                            label,
                          })
                        }
                      >
                        Approve exception
                      </button>
                      <button
                        type="button"
                        className="rounded-sm border border-critical/40 px-3 py-2 text-caption text-critical disabled:opacity-40"
                        disabled={
                          !label.trim() ||
                          !revokeReason.trim() ||
                          decision.isPending
                        }
                        onClick={() =>
                          decision.mutate({
                            action: "revoke",
                            exceptionId: row.id,
                            label,
                            reason: revokeReason,
                          })
                        }
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="text-caption text-slate">
                        Awaiting approval by a different officer with
                        Confidential · Approve.
                      </p>
                      <Link
                        href="/access"
                        className="text-caption font-medium text-action hover:underline"
                      >
                        Request access
                      </Link>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {error && (
        <p role="alert" className="text-body text-critical">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-body text-success">
          {notice}
        </p>
      )}
    </section>
  );
}
