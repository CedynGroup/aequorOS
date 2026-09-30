"use client";

/**
 * Threshold alerts — tell me when a figure crosses a line I set.
 *
 * Each alert is judged when this institution's figures are rebuilt for a
 * reporting date, against a number the bank states or the limit already governed
 * for the figure. Its owner is re-authorized at every evaluation and every person
 * it is addressed to is authorized separately before the figure reaches them, so
 * an alert carries nobody's access but its reader's own.
 *
 * Only the person who created an alert can change it. Not an account
 * administrator, not an Org Owner — the server decides that, and this page simply
 * does not offer the controls for somebody else's alert.
 */

import { useMemo, useState } from "react";
import { BellOff, BellRing, Pencil, Plus, Trash2 } from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import AlertComposer from "@/components/bi/AlertComposer";
import AlertHistory from "@/components/bi/AlertHistory";
import { valueTypeLabel } from "@/components/bi/notifications";
import {
  biRefusalSentence,
  isBiAccessDenied,
  isBiUnavailable,
  unknownRecipients,
  useBiAlertEvents,
  useBiAlerts,
  useBiCatalogue,
  useCreateBiAlert,
  useDeactivateBiAlert,
  useDeleteBiAlert,
  useUpdateBiAlert,
  type BiAlertRead,
  type BiAlertUpsert,
} from "@/lib/api/bi";
import { useBiNotificationCapabilities } from "@/lib/api/hooks";

/**
 * Copy for the deployment switch, used wherever the page would otherwise imply
 * the platform is waiting on the bank. `BI_ALERTS_ENABLED` is the operator's,
 * not the bank's, so the sentence names the deployment and says what happens
 * to an alert created meanwhile (it is kept, not judged).
 */
const EVALUATION_OFF =
  "Alert evaluation is not enabled in this deployment. Alerts you create are kept, but none is judged until your platform operator enables it.";

function stateTone(alert: BiAlertRead): StatusTone {
  if (!alert.isActive) return "slate";
  if (alert.latestState === "breached") return "approaching";
  if (alert.latestState === "cleared") return "compliant";
  return "pending";
}

/**
 * The pill on a row. An alert with no verdict yet is said to be waiting for the
 * bank's figures ONLY when the deployment has said it evaluates alerts: with
 * the switch off, the platform is not waiting on the bank's book, and saying so
 * blamed the bank's data for the operator's switch (audit A360-2 M3). When the
 * platform has not said either way, the pill claims nothing about who is
 * waiting.
 */
function stateLabel(
  alert: BiAlertRead,
  evaluationEnabled: boolean | undefined,
): string {
  if (!alert.isActive) return "Stopped";
  if (alert.latestState === "breached") return "Past its threshold";
  if (alert.latestState === "cleared") return "Within its threshold";
  if (alert.latestState === "not_evaluated") return "Not judged";
  if (evaluationEnabled === true) return "Waiting for figures";
  if (evaluationEnabled === false) return "Not judged here";
  return "No verdict yet";
}

export default function BiAlertsPage() {
  const { bank } = useBankContext();
  const capability = useBiNotificationCapabilities();
  const evaluationEnabled = capability.alerts;
  const alerts = useBiAlerts(bank?.id);
  const catalogue = useBiCatalogue(bank?.id);
  const create = useCreateBiAlert(bank?.id);
  const update = useUpdateBiAlert(bank?.id);
  const deactivate = useDeactivateBiAlert(bank?.id);
  const remove = useDeleteBiAlert(bank?.id);

  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState<BiAlertRead | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  const events = useBiAlertEvents(bank?.id, opened);
  const openedAlert = useMemo(
    () => (alerts.data ?? []).find((alert) => alert.id === opened) ?? null,
    [alerts.data, opened],
  );

  const saving = create.isPending || update.isPending;
  const failure = create.error ?? update.error ?? null;
  const rows = alerts.data ?? [];

  function submit(body: BiAlertUpsert): void {
    if (editing) {
      update.mutate(
        { alertId: editing.id, body },
        {
          onSuccess: () => {
            setEditing(null);
            setComposing(false);
          },
        },
      );
      return;
    }
    create.mutate(body, {
      onSuccess: () => {
        setComposing(false);
      },
    });
  }

  if (isBiUnavailable(alerts.error)) {
    return (
      <>
        <PageHeader title="Threshold alerts" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={BellRing}
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace, so there is nothing to set a threshold on. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Threshold alerts"
        subtitle="Watch one figure against a line and be told when it crosses. Each person you name is checked against their own access before the figure reaches them."
        action={
          !composing && (
            <button
              type="button"
              onClick={() => {
                setEditing(null);
                setComposing(true);
              }}
              className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white"
            >
              <Plus size={13} aria-hidden />
              New alert
            </button>
          )
        }
      />

      <PageContainer className="flex flex-col gap-6 py-6">
        {evaluationEnabled === false && (
          <div
            role="status"
            className="flex items-start gap-2.5 rounded-md border border-border bg-surface px-4 py-3 text-caption text-navy"
          >
            <BellOff size={15} className="mt-0.5 shrink-0 text-slate" aria-hidden />
            <p>
              <span className="font-medium">Not judging alerts here.</span>{" "}
              {EVALUATION_OFF}
            </p>
          </div>
        )}

        {composing && (
          <SectionCard
            title={editing ? "Change this alert" : "New threshold alert"}
            subtitle={
              evaluationEnabled === false
                ? `Only figures your own access covers are offered. ${EVALUATION_OFF}`
                : "Only figures your own access covers are offered."
            }
          >
            <AlertComposer
              measures={catalogue.data?.measures ?? []}
              editing={editing}
              saving={saving}
              errorMessage={
                failure
                  ? failure instanceof Error
                    ? failure.message
                    : "This alert could not be saved."
                  : null
              }
              unknownAddresses={unknownRecipients(failure)}
              onSubmit={submit}
              onCancel={() => {
                setComposing(false);
                setEditing(null);
                create.reset();
                update.reset();
              }}
            />
          </SectionCard>
        )}

        {alerts.isError && !isBiUnavailable(alerts.error) && (
          <ErrorPanel
            error={alerts.error}
            title="Could not read this institution's alerts"
            onRetry={() => void alerts.refetch()}
          />
        )}

        {alerts.isPending ? (
          <p className="text-caption text-slate">Reading your alerts…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            Icon={BellRing}
            title="No alerts yet"
            description={
              evaluationEnabled === false
                ? `Nothing is being watched for you. An alert names one figure, a direction and a line — either a number you set or the limit already governed for that figure. ${EVALUATION_OFF}`
                : "Nothing is being watched for you. An alert names one figure, a direction and a line — either a number you set or the limit already governed for that figure — and it is judged every time this institution's figures are rebuilt."
            }
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((alert) => (
              <li key={alert.id}>
                <SectionCard
                  title={
                    <span className="flex flex-wrap items-center gap-2">
                      {alert.name}
                      <StatusPill tone={stateTone(alert)}>
                        {stateLabel(alert, evaluationEnabled)}
                      </StatusPill>
                      {!alert.ownedByCaller && (
                        <span className="text-micro font-normal text-slate">
                          Created by{" "}
                          {alert.ownerDisplayName ?? "another colleague"}
                        </span>
                      )}
                    </span>
                  }
                  subtitle={
                    <>
                      {alert.measureLabel}{" "}
                      {alert.direction === "above"
                        ? "rising above"
                        : "falling below"}{" "}
                      {alert.thresholdBasis === "governed_limit"
                        ? "the limit governed for it"
                        : `${alert.threshold}`}
                      {alert.thresholdBasis === "stated" ? " " : ""}
                      {alert.thresholdBasis === "stated" && (
                        <span className="text-slate">
                          (
                          {valueTypeLabel(
                            catalogue.data?.measures.find(
                              (measure) => measure.id === alert.measureId,
                            )?.valueType,
                          )}
                          )
                        </span>
                      )}
                    </>
                  }
                  actions={
                    alert.ownedByCaller && (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            setEditing(alert);
                            setComposing(true);
                            create.reset();
                            update.reset();
                          }}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-slate hover:bg-surface"
                        >
                          <Pencil size={12} aria-hidden />
                          Change
                        </button>
                        {alert.isActive && (
                          <button
                            type="button"
                            disabled={deactivate.isPending}
                            onClick={() =>
                              deactivate.mutate({
                                alertId: alert.id,
                                reason:
                                  "Stopped from the threshold alerts workspace.",
                              })
                            }
                            className="rounded-md border border-border px-2 py-1 text-caption text-slate hover:bg-surface disabled:opacity-50"
                          >
                            Stop
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={remove.isPending}
                          onClick={() => remove.mutate(alert.id)}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-critical hover:bg-critical-light disabled:opacity-50"
                        >
                          <Trash2 size={12} aria-hidden />
                          Delete
                        </button>
                      </div>
                    )
                  }
                >
                  <p className="text-body leading-relaxed text-navy">
                    {alert.latestDetail}
                  </p>
                  {alert.ownedByCaller && alert.recipients.length > 0 && (
                    <p className="mt-2 text-caption text-slate">
                      Tells{" "}
                      {alert.recipients
                        .map(
                          (person) =>
                            `${person.displayName ?? person.email}${
                              person.isActive ? "" : " (account not active)"
                            }`,
                        )
                        .join(", ")}
                      .
                    </p>
                  )}
                  {alert.ownedByCaller && alert.recipients.length === 0 && (
                    <p className="mt-2 text-caption text-slate">
                      Nobody is told; the verdict is recorded only.
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      setOpened(opened === alert.id ? null : alert.id)
                    }
                    className="mt-3 text-caption font-medium text-action"
                  >
                    {opened === alert.id
                      ? "Hide what was recorded"
                      : "Show what was recorded"}
                  </button>
                  {opened === alert.id && (
                    <div className="mt-3 border-t border-border-light pt-3">
                      <AlertHistory
                        events={events.data ?? []}
                        loading={events.isPending}
                        // A refusal is never rendered as "nothing recorded". A
                        // 403 that is not a grant denial (a read-only staff
                        // session, an impersonated principal) shows the
                        // server's own sentence; a grant denial keeps the
                        // paraphrase the disclosure rule requires; any other
                        // failure says the read failed rather than that there
                        // is nothing to read.
                        withheldMessage={
                          biRefusalSentence(events.error) ??
                          (isBiAccessDenied(events.error)
                            ? (openedAlert?.latestDetail ??
                              "Your access does not cover the figure this alert watches.")
                            : events.isError
                              ? "What was recorded for this alert could not be read just now."
                              : null)
                        }
                      />
                    </div>
                  )}
                </SectionCard>
              </li>
            ))}
          </ul>
        )}

        {remove.isError && (
          <ErrorPanel
            error={remove.error}
            title="Could not delete that alert"
          />
        )}
        {deactivate.isError && (
          <ErrorPanel
            error={deactivate.error}
            title="Could not stop that alert"
          />
        )}
      </PageContainer>
    </>
  );
}
