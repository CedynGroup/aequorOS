"use client";

/**
 * Scheduled reports — mail this question to these people on this schedule.
 *
 * Every copy is prepared under its RECIPIENT's own access, never the author's, so
 * naming somebody here gives them nothing: a person whose access does not cover
 * the figures is sent nothing at all, and the send history says so plainly rather
 * than hiding it as an error.
 *
 * A report whose figures identify individual records is never attached. That is
 * decided from the catalogue's own sensitivity declarations before a single row
 * is read, so those reports are sent as a sign-in link and the composer says so
 * while the question is still being built.
 *
 * Only the person who created a report can change it, and only they can see who
 * else receives it: being on a distribution list is not being told who else is.
 */

import { useState } from "react";
import {
  CalendarClock,
  CalendarOff,
  Link2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import SectionCard from "@/components/ui/SectionCard";
import EmptyState from "@/components/ui/EmptyState";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBankContext } from "@/components/shell/BankContext";
import DeliveryHistory from "@/components/bi/DeliveryHistory";
import SubscriptionComposer from "@/components/bi/SubscriptionComposer";
import {
  FORMAT_OPTIONS,
  scheduleSentence,
} from "@/components/bi/notifications";
import {
  biRefusalSentence,
  isBiAccessDenied,
  isBiUnavailable,
  isoDay,
  unknownRecipients,
  useBiCatalogue,
  useBiSubscriptionDeliveries,
  useBiSubscriptions,
  useCreateBiSubscription,
  useDeactivateBiSubscription,
  useDeleteBiSubscription,
  useUpdateBiSubscription,
  type BiSubscriptionRead,
  type BiSubscriptionUpsert,
} from "@/lib/api/bi";
import { useBiNotificationCapabilities } from "@/lib/api/hooks";
import type { StatusTone } from "@/components/ui/StatusPill";

/**
 * Copy for the deployment switch. `BI_SUBSCRIPTIONS_ENABLED` is the operator's,
 * not the bank's: with it off the scheduler never picks a report up, so a row
 * must not read "Sending" and the page must say whose switch it is.
 */
const DELIVERY_OFF =
  "Scheduled report delivery is not enabled in this deployment. Reports you create are kept, but none goes out until your platform operator enables it.";

function formatLabel(code: string): string {
  return FORMAT_OPTIONS.find((option) => option.value === code)?.label ?? code;
}

/**
 * The pill on a row. "Sending" is claimed ONLY when the deployment has said it
 * delivers reports; with the switch off the row says so, and when the platform
 * has not said either way the pill states the schedule and nothing more.
 */
function sendingState(
  subscription: BiSubscriptionRead,
  deliveryEnabled: boolean | undefined,
): { label: string; tone: StatusTone } {
  if (!subscription.isActive) return { label: "Stopped", tone: "slate" };
  if (deliveryEnabled === true) return { label: "Sending", tone: "compliant" };
  if (deliveryEnabled === false) return { label: "Not sending here", tone: "pending" };
  return { label: "Scheduled", tone: "pending" };
}

export default function BiSubscriptionsPage() {
  const { bank, period } = useBankContext();
  const capability = useBiNotificationCapabilities();
  const deliveryEnabled = capability.subscriptions;
  const subscriptions = useBiSubscriptions(bank?.id);
  const catalogue = useBiCatalogue(bank?.id);
  const create = useCreateBiSubscription(bank?.id);
  const update = useUpdateBiSubscription(bank?.id);
  const deactivate = useDeactivateBiSubscription(bank?.id);
  const remove = useDeleteBiSubscription(bank?.id);

  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState<BiSubscriptionRead | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  const deliveries = useBiSubscriptionDeliveries(bank?.id, opened);
  const rows = subscriptions.data ?? [];
  const saving = create.isPending || update.isPending;
  const failure = create.error ?? update.error ?? null;

  // The date the stored question is SHAPED for. Every run rebinds it to the
  // institution's own latest reporting date, which is why the composer says so
  // and why this is never offered as a control.
  const shapeDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  // The institution's OWN zone. A saved report carries the server's own answer
  // and is preferred; before the first one exists the bank payload carries the
  // SAME registry value (`jurisdictions.timezone`, resolved through
  // `banks.jurisdiction_code`), so it is read from there rather than from a row
  // that may not exist yet. Reading a missing row as UTC is what made a user in
  // Lagos or Nairobi set 07:30 believing one thing while the platform sent at
  // another.
  //
  // The final `"UTC"` is not a country literal standing in for a jurisdiction:
  // it is the server's OWN stated fallback for a registry row with no zone
  // recorded (`services/bi/subscriptions._zone_for`, mirrored by
  // `manage_bi_notifications._time_zone_name` and pinned equal by a test). So
  // when it is shown it is what the delivery scan will actually use — which is
  // the only thing this label is allowed to claim.
  const timeZone = rows[0]?.timeZone ?? bank?.jurisdiction?.timezone ?? "UTC";

  function submit(body: BiSubscriptionUpsert): void {
    if (editing) {
      update.mutate(
        { subscriptionId: editing.id, body },
        {
          onSuccess: () => {
            setEditing(null);
            setComposing(false);
          },
        },
      );
      return;
    }
    create.mutate(body, { onSuccess: () => setComposing(false) });
  }

  if (isBiUnavailable(subscriptions.error)) {
    return (
      <>
        <PageHeader title="Scheduled reports" />
        <PageContainer className="py-6">
          <EmptyState
            Icon={CalendarClock}
            title="Business intelligence is not available here"
            description="This institution does not serve the analytics workspace, so there is nothing to schedule. If you expected it, ask your organization owner to check with support."
          />
        </PageContainer>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Scheduled reports"
        subtitle="Send a question to a list of people on a schedule. Each copy is prepared under that person's own access, so nobody receives figures they could not have asked for."
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
              New report
            </button>
          )
        }
      />

      <PageContainer className="flex flex-col gap-6 py-6">
        {deliveryEnabled === false && (
          <div
            role="status"
            className="flex items-start gap-2.5 rounded-md border border-border bg-surface px-4 py-3 text-caption text-navy"
          >
            <CalendarOff size={15} className="mt-0.5 shrink-0 text-slate" aria-hidden />
            <p>
              <span className="font-medium">Not sending reports here.</span>{" "}
              {DELIVERY_OFF}
            </p>
          </div>
        )}

        {composing && (
          <SectionCard
            title={editing ? "Change this report" : "New scheduled report"}
            subtitle={
              deliveryEnabled === false
                ? `Only figures your own access covers are offered. ${DELIVERY_OFF}`
                : "Only figures your own access covers are offered."
            }
          >
            <SubscriptionComposer
              catalogue={catalogue.data}
              asOf={shapeDate}
              timeZone={editing?.timeZone ?? timeZone}
              editing={editing}
              saving={saving}
              errorMessage={
                failure
                  ? failure instanceof Error
                    ? failure.message
                    : "This report could not be saved."
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

        {subscriptions.isError && !isBiUnavailable(subscriptions.error) && (
          <ErrorPanel
            error={subscriptions.error}
            title="Could not read this institution's scheduled reports"
            onRetry={() => void subscriptions.refetch()}
          />
        )}

        {subscriptions.isPending ? (
          <p className="text-caption text-slate">
            Reading your scheduled reports…
          </p>
        ) : rows.length === 0 ? (
          <EmptyState
            Icon={CalendarClock}
            title="No scheduled reports yet"
            description={
              deliveryEnabled === false
                ? `Nothing is being sent for you. A report names the figures to include, how often to send them and who receives them. ${DELIVERY_OFF}`
                : "Nothing is being sent for you. A report names the figures to include, how often to send them and who receives them — and each person's copy is prepared under their own access when it goes out."
            }
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((subscription) => (
              <li key={subscription.id}>
                <SectionCard
                  title={
                    <span className="flex flex-wrap items-center gap-2">
                      {subscription.name}
                      <StatusPill
                        tone={sendingState(subscription, deliveryEnabled).tone}
                      >
                        {sendingState(subscription, deliveryEnabled).label}
                      </StatusPill>
                      {subscription.disclosureClass === "record_level" && (
                        <span className="inline-flex items-center gap-1 text-micro text-slate">
                          <Link2 size={12} aria-hidden />
                          Sent as a sign-in link
                        </span>
                      )}
                      {!subscription.ownedByCaller && (
                        <span className="text-micro font-normal text-slate">
                          Created by{" "}
                          {subscription.ownerDisplayName ?? "another colleague"}
                        </span>
                      )}
                    </span>
                  }
                  subtitle={scheduleSentence(
                    subscription.cadence,
                    {
                      hour: subscription.hour,
                      minute: subscription.minute,
                      dayOfWeek: subscription.dayOfWeek,
                      dayOfMonth: subscription.dayOfMonth,
                    },
                    subscription.timeZone,
                  )}
                  actions={
                    subscription.ownedByCaller && (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            setEditing(subscription);
                            setComposing(true);
                            create.reset();
                            update.reset();
                          }}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption text-slate hover:bg-surface"
                        >
                          <Pencil size={12} aria-hidden />
                          Change
                        </button>
                        {subscription.isActive && (
                          <button
                            type="button"
                            disabled={deactivate.isPending}
                            onClick={() =>
                              deactivate.mutate({
                                subscriptionId: subscription.id,
                                reason:
                                  "Stopped from the scheduled reports workspace.",
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
                          onClick={() => remove.mutate(subscription.id)}
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
                    {subscription.deliveryNote}
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    {formatLabel(subscription.artifactFormat)}.
                    {subscription.ownedByCaller &&
                    subscription.recipients.length > 0
                      ? ` Sent to ${subscription.recipients
                          .map(
                            (person) =>
                              `${person.displayName ?? person.email}${
                                person.isActive ? "" : " (account not active)"
                              }`,
                          )
                          .join(", ")}.`
                      : ""}
                  </p>
                  {subscription.ownedByCaller && (
                    <>
                      <button
                        type="button"
                        onClick={() =>
                          setOpened(
                            opened === subscription.id ? null : subscription.id,
                          )
                        }
                        className="mt-3 text-caption font-medium text-action"
                      >
                        {opened === subscription.id
                          ? "Hide the send history"
                          : "Show the send history"}
                      </button>
                      {opened === subscription.id && (
                        <div className="mt-3 border-t border-border-light pt-3">
                          {(() => {
                            // Same rule as the alert history: a refusal or a
                            // failed read is never shown as "nothing sent yet".
                            const withheld =
                              biRefusalSentence(deliveries.error) ??
                              (isBiAccessDenied(deliveries.error)
                                ? "Your access does not cover the figures this report sends, so its send history is not shown."
                                : deliveries.isError
                                  ? "The send history for this report could not be read just now."
                                  : null);
                            return withheld ? (
                              <p className="text-caption text-slate">{withheld}</p>
                            ) : (
                              <DeliveryHistory
                                deliveries={deliveries.data ?? []}
                                loading={deliveries.isPending}
                              />
                            );
                          })()}
                        </div>
                      )}
                    </>
                  )}
                </SectionCard>
              </li>
            ))}
          </ul>
        )}

        {remove.isError && (
          <ErrorPanel
            error={remove.error}
            title="Could not delete that report"
          />
        )}
        {deactivate.isError && (
          <ErrorPanel
            error={deactivate.error}
            title="Could not stop that report"
          />
        )}
      </PageContainer>
    </>
  );
}
