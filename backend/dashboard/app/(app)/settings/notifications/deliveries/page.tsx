"use client";

import { useState } from "react";
import type { DeliveryStatus } from "@aequoros/risk-service-api";

import { Card, CardBody } from "@/components/ui/Card";
import PageHeader from "@/components/ui/PageHeader";
import { SkeletonTable } from "@/components/ui/Skeleton";
import {
  DELIVERY_PAGE_SIZE,
  useNotificationDeliveries,
  useNotificationOrgSettings,
  useRetryNotificationDelivery,
} from "@/lib/api/notificationSettings";
import { fmtRelative } from "@/lib/api/values";
import {
  DELIVERY_STATUS_LABELS,
  deliveryReason,
  untilLabel,
} from "@/lib/notifications/copy";

const FILTERS: { key: DeliveryStatus | null; label: string }[] = [
  { key: null, label: "All" },
  { key: "sent", label: DELIVERY_STATUS_LABELS.sent },
  { key: "pending", label: DELIVERY_STATUS_LABELS.pending },
  { key: "dead", label: DELIVERY_STATUS_LABELS.dead },
  { key: "suppressed", label: DELIVERY_STATUS_LABELS.suppressed },
];

const STATUS_STYLES: Record<DeliveryStatus, string> = {
  sent: "bg-success-light text-success",
  pending: "bg-action-light text-action",
  dead: "bg-critical-light text-critical",
  suppressed: "bg-surface-alt text-slate",
};

export default function NotificationDeliveriesPage() {
  const admin = useNotificationOrgSettings();
  const [status, setStatus] = useState<DeliveryStatus | null>(null);
  const [offset, setOffset] = useState(0);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Notifications", href: "/settings/notifications" },
          { label: "Delivery log" },
        ]}
        title="Email delivery log"
        subtitle="Every notification email AequorOS has sent, is about to send, or held back — and why."
      />
      <div className="px-4 md:px-8 py-6 max-w-6xl">
        {admin.isLoading ? (
          <SkeletonTable rows={6} />
        ) : admin.data === null ? (
          <Card>
            <CardBody>
              <p className="text-body text-navy">
                Only account administrators can see the delivery log.
              </p>
            </CardBody>
          </Card>
        ) : (
          <DeliveryTable
            status={status}
            offset={offset}
            onStatus={(next) => {
              setStatus(next);
              setOffset(0);
            }}
            onOffset={setOffset}
          />
        )}
      </div>
    </>
  );
}

function DeliveryTable({
  status,
  offset,
  onStatus,
  onOffset,
}: {
  status: DeliveryStatus | null;
  offset: number;
  onStatus: (status: DeliveryStatus | null) => void;
  onOffset: (offset: number) => void;
}) {
  const log = useNotificationDeliveries(status, offset);
  const retry = useRetryNotificationDelivery();
  const [retryError, setRetryError] = useState<string | null>(null);
  const counts = log.data?.counts ?? {};
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);

  return (
    <div className="space-y-4">
      <nav aria-label="Filter by status" className="flex flex-wrap gap-2">
        {FILTERS.map((filter) => {
          const active = filter.key === status;
          const count = filter.key === null ? total : (counts[filter.key] ?? 0);
          return (
            <button
              key={filter.label}
              type="button"
              aria-pressed={active}
              onClick={() => onStatus(filter.key)}
              className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                active
                  ? "bg-action text-white border-action"
                  : "bg-surface text-slate border-border hover:text-navy"
              }`}
            >
              {filter.label} <span className="opacity-75">({count})</span>
            </button>
          );
        })}
      </nav>
      {retryError && (
        <p role="alert" className="text-caption text-danger">
          {retryError}
        </p>
      )}
      {log.isLoading ? (
        <SkeletonTable rows={6} />
      ) : log.error || !log.data ? (
        <Card>
          <CardBody>
            <p className="text-body text-danger">
              Could not load the delivery log. {log.error?.message}
            </p>
          </CardBody>
        </Card>
      ) : log.data.deliveries.length === 0 ? (
        <Card>
          <CardBody>
            <p className="text-body text-slate">
              {status === null
                ? "No notification emails yet."
                : `Nothing ${DELIVERY_STATUS_LABELS[status].toLowerCase()} right now.`}
            </p>
          </CardBody>
        </Card>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-body">
              <thead>
                <tr className="text-left text-caption text-slate border-b border-border-light">
                  <th className="px-5 py-2 font-medium">When</th>
                  <th className="px-3 py-2 font-medium">To</th>
                  <th className="px-3 py-2 font-medium">Notification</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-5 py-2 font-medium sr-only">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-light">
                {log.data.deliveries.map((delivery) => {
                  const reason = deliveryReason(delivery.lastError);
                  const canRetry =
                    delivery.status === "dead" || delivery.status === "suppressed";
                  return (
                    <tr key={delivery.id}>
                      <td className="px-5 py-3 align-top whitespace-nowrap text-caption text-slate">
                        {fmtRelative(delivery.sentAt ?? delivery.createdAt)}
                        {delivery.mode === "digest" && (
                          <span className="block">In the daily summary</span>
                        )}
                      </td>
                      <td className="px-3 py-3 align-top">
                        <p className="font-medium text-navy">{delivery.recipientName}</p>
                        <p className="text-caption text-slate">{delivery.address}</p>
                      </td>
                      <td className="px-3 py-3 align-top">
                        <p className="text-navy">
                          {delivery.title ?? (
                            <span className="italic text-slate">
                              Hidden — you can&rsquo;t see this notice
                            </span>
                          )}
                        </p>
                        <p className="text-caption text-slate">{delivery.eventLabel}</p>
                      </td>
                      <td className="px-3 py-3 align-top">
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-caption font-medium ${STATUS_STYLES[delivery.status]}`}
                        >
                          {DELIVERY_STATUS_LABELS[delivery.status]}
                        </span>
                        {delivery.attempts > 1 && (
                          <p className="mt-1 text-caption text-slate">
                            {delivery.attempts} attempts
                          </p>
                        )}
                        {delivery.status === "pending" && delivery.attempts > 0 && (
                          <p className="mt-1 text-caption text-slate">
                            Trying again {untilLabel(delivery.nextAttemptAt)}
                          </p>
                        )}
                        {reason && (
                          <p className="mt-1 text-caption text-slate max-w-xs">{reason}</p>
                        )}
                      </td>
                      <td className="px-5 py-3 align-top text-right">
                        {canRetry && (
                          <button
                            type="button"
                            disabled={retry.isPending}
                            onClick={() => {
                              setRetryError(null);
                              retry.mutate(delivery.id, {
                                onError: (error) =>
                                  setRetryError(
                                    error instanceof Error
                                      ? error.message
                                      : "Could not queue this email again.",
                                  ),
                              });
                            }}
                            className="px-3 py-1.5 rounded-sm border border-border text-caption font-medium text-navy hover:bg-surface-alt disabled:opacity-60"
                          >
                            Send again
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between px-5 py-3 border-t border-border-light text-caption text-slate">
            <span>
              {offset + 1}–{offset + log.data.deliveries.length} of {log.data.total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => onOffset(Math.max(0, offset - DELIVERY_PAGE_SIZE))}
                className="px-3 py-1.5 rounded-sm border border-border font-medium text-navy disabled:opacity-40"
              >
                Newer
              </button>
              <button
                type="button"
                disabled={!log.data.hasMore}
                onClick={() => onOffset(offset + DELIVERY_PAGE_SIZE)}
                className="px-3 py-1.5 rounded-sm border border-border font-medium text-navy disabled:opacity-40"
              >
                Older
              </button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
