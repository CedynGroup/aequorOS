"use client";

/**
 * Whether the standardised framework is required for this reporting date.
 *
 * The copy has to be accurate on BOTH sides of a date that has not arrived:
 * before it, the interim economic-value method is the correct basis and this
 * card must not imply otherwise; from it, the interim method is superseded and
 * a report still using it cannot be frozen. And the date itself is a governed
 * console row seeded pending confirmation — so it is never presented as
 * settled unless the server says it is.
 *
 * Every sentence comes from `labels.ts` or from the server; none is composed
 * here.
 */

import StatusPill from "@/components/ui/StatusPill";
import type { SfMandate } from "@/lib/api/irrbbSfNormalize";
import { mandateCopy, PENDING_CHIP } from "./labels";

/** An absent mandate object reads exactly as an unreadable one — see labels. */
const UNREAD = { stated: false } as SfMandate;

export default function MandateCard({ mandate }: { mandate: SfMandate }) {
  const rule = mandate ?? UNREAD;
  const copy = mandateCopy(rule);
  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-body font-medium text-navy">{copy.heading}</p>
        {copy.qualifier === null ? null : (
          <StatusPill tone="pending">{PENDING_CHIP}</StatusPill>
        )}
      </div>
      <p className="mt-1 text-body leading-relaxed text-navy/80">{copy.body}</p>
      {copy.qualifier === null ? null : (
        <p className="mt-2 text-caption leading-relaxed text-warning">
          {copy.qualifier}
        </p>
      )}
      {rule.sourceCitation ? (
        <p className="mt-2 text-caption text-slate">{rule.sourceCitation}</p>
      ) : null}
    </div>
  );
}
