"use client";

/**
 * What each recipient of one report was actually sent.
 *
 * THE POINT OF THIS PANEL IS THE REFUSED ROW. A recipient whose access does not
 * cover the figures is sent nothing, and that is the platform working: the report
 * was not theirs to receive. So `denied` is shown neutrally, with the server's own
 * sentence about access, and it is not counted as a failure — only a send the
 * relay would not accept is. Hiding a refused row would leave an owner believing
 * a colleague has a pack they have never seen, which is worse than either.
 *
 * Nothing here is a figure. A row count and a file size are facts about a file;
 * the file itself was never produced for a refused recipient, which is why those
 * columns are empty for one rather than zero.
 */

import { CircleSlash, Clock, Link2, Mail, TriangleAlert } from "lucide-react";
import { deliveryTone } from "@/components/bi/notifications";
import EmptyState from "@/components/ui/EmptyState";
import { fmtInt } from "@/lib/format";
import type { BiSubscriptionDeliveryRead } from "@/lib/api/bi";

const TONE_CLASS: Readonly<Record<string, string>> = {
  positive: "bg-success-light text-success border-success/20",
  neutral: "bg-surface text-slate border-border",
  warning: "bg-warning-light text-warning border-warning/20",
  pending: "bg-surface text-slate border-border",
};

function Icon({ delivery }: { delivery: BiSubscriptionDeliveryRead }) {
  if (delivery.status === "failed") {
    return <TriangleAlert size={13} aria-hidden />;
  }
  if (delivery.status === "denied" || delivery.status === "no_data") {
    return <CircleSlash size={13} aria-hidden />;
  }
  if (delivery.status === "pending") return <Clock size={13} aria-hidden />;
  return delivery.deliveryMode === "link" ? (
    <Link2 size={13} aria-hidden />
  ) : (
    <Mail size={13} aria-hidden />
  );
}

function when(value: string): string {
  // The wire carries an instant; the reader wants the minute it was due, in
  // their own locale, so this is the one place a browser-local rendering is
  // correct — the schedule itself is always shown in the institution's zone.
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export default function DeliveryHistory({
  deliveries,
  loading,
}: {
  deliveries: readonly BiSubscriptionDeliveryRead[];
  loading: boolean;
}) {
  if (loading) {
    return <p className="text-caption text-slate">Reading the send history…</p>;
  }
  if (deliveries.length === 0) {
    return (
      <EmptyState
        Icon={Mail}
        title="Nothing has been sent yet"
        description="This report has not reached its first send. Each recipient will appear here with what they were sent, including anyone whose access did not cover the figures."
      />
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-border-light">
      {deliveries.map((delivery) => {
        const tone = deliveryTone(delivery.status);
        return (
          <li key={delivery.id} className="flex flex-col gap-1 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-micro font-medium ${TONE_CLASS[tone]}`}
              >
                <Icon delivery={delivery} />
                {delivery.status === "sent" && delivery.deliveryMode === "link"
                  ? "Sign-in link sent"
                  : delivery.detail.split(".")[0]}
              </span>
              <span className="text-body text-navy">
                {delivery.recipientDisplayName ??
                  delivery.recipientEmail ??
                  "A recipient of this report"}
              </span>
              <span className="text-micro text-slate">
                {when(delivery.scheduledFor)}
              </span>
              {delivery.trigger === "new_data" && (
                <span className="text-micro text-slate">
                  Sent because new figures arrived
                </span>
              )}
            </div>
            <p className="text-caption leading-relaxed text-slate">
              {delivery.detail}
            </p>
            {delivery.rowCount !== null && (
              <p className="text-micro text-slate">
                {fmtInt(delivery.rowCount)}{" "}
                {delivery.rowCount === 1 ? "row" : "rows"}
                {delivery.asOfDate ? ` as at ${delivery.asOfDate}` : ""}
                {delivery.artifactSizeBytes !== null
                  ? `, ${Math.max(1, Math.round(delivery.artifactSizeBytes / 1024))} KB`
                  : ""}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
