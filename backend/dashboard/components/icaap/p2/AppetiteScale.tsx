"use client";

/**
 * One metric's appetite scale, drawn with the safe side on the right.
 *
 * Four markers: the governing regulatory value (when one is governed), the
 * bank's capacity, its tolerance and its appetite, plus the current reading.
 * Every one of them is a value that was entered by the bank or sent by the API.
 *
 * D-036 is the reason this component has a second mode. When the control plane
 * governs no value for the metric — the Ghana LCR Directive is not public, so
 * `lcr_min` is ungoverned — the scale does not draw a regulatory marker and the
 * caption reads "not assessed against a regulatory floor". It never substitutes
 * a number, and it never treats the absence as a pass.
 *
 * The ordering check shown here is the CLIENT MIRROR (`lib/icaap/appetite.ts`).
 * The server's 422 `violations` are authoritative and are rendered above this
 * component verbatim; this is only so the preparer sees it while typing.
 */

import {
  checkAppetiteOrdering,
  scaleDomain,
  scaleFraction,
  type AppetiteDirection,
} from "@/lib/icaap/appetite";
import { numOrNull } from "@/lib/api/values";
import { PERCENT_BASE } from "./display";
import { NO_REGULATORY_FLOOR, NOT_ASSESSED, fmtInUnit, orderingSentence } from "./labels";

type Marker = {
  key: string;
  label: string;
  value: number | null;
  tone: string;
};

export default function AppetiteScale({
  direction,
  unit,
  appetite,
  tolerance,
  capacity,
  reference,
  current,
  referenceMissing,
}: {
  direction: AppetiteDirection;
  unit: string;
  appetite?: string | null;
  tolerance?: string | null;
  capacity?: string | null;
  /** The governed regulatory value, or null when none is governed (D-036). */
  reference?: string | null;
  current?: string | null;
  referenceMissing: boolean;
}) {
  const values = {
    appetite: numOrNull(appetite),
    tolerance: numOrNull(tolerance),
    capacity: numOrNull(capacity),
    reference: numOrNull(reference),
    current: numOrNull(current),
  };

  const ordering = checkAppetiteOrdering(direction, {
    appetite,
    tolerance,
    capacity,
    reference,
  });

  const markers: Marker[] = [
    {
      key: "reference",
      label: "Regulatory",
      value: values.reference,
      tone: "bg-navy",
    },
    {
      key: "capacity",
      label: "Capacity",
      value: values.capacity,
      tone: "bg-critical",
    },
    {
      key: "tolerance",
      label: "Tolerance",
      value: values.tolerance,
      tone: "bg-warning",
    },
    {
      key: "appetite",
      label: "Appetite",
      value: values.appetite,
      tone: "bg-success",
    },
  ];

  const domain = scaleDomain(
    [...markers.map((marker) => marker.value), values.current],
  );

  return (
    <div className="space-y-2">
      {domain === null ? (
        <p className="text-caption text-slate">
          There are not enough levels set to draw a scale yet.
        </p>
      ) : (
        <div className="relative h-10 rounded bg-gradient-to-r from-critical-light via-warning-light to-success-light">
          {markers
            .filter((marker) => marker.value !== null)
            .map((marker) => (
              <span
                key={marker.key}
                title={`${marker.label}: ${fmtInUnit(marker.value, unit)}`}
                className={`absolute top-0 h-full w-0.5 ${marker.tone}`}
                style={{
                  left: `${scaleFraction(marker.value as number, domain, direction) * PERCENT_BASE}%`,
                }}
              >
                <span className="sr-only">
                  {marker.label} {fmtInUnit(marker.value, unit)}
                </span>
              </span>
            ))}
          {values.current !== null && (
            <span
              title={`Current: ${fmtInUnit(values.current, unit)}`}
              className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-navy"
              style={{
                left: `${scaleFraction(values.current, domain, direction) * PERCENT_BASE}%`,
              }}
            >
              <span className="sr-only">
                Current reading {fmtInUnit(values.current, unit)}
              </span>
            </span>
          )}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-caption sm:grid-cols-4">
        {markers.map((marker) => (
          <div key={marker.key}>
            <dt className="text-slate">{marker.label}</dt>
            <dd className="tnum text-navy">
              {marker.key === "reference" && referenceMissing
                ? "Not governed"
                : fmtInUnit(marker.value, unit, NOT_ASSESSED)}
            </dd>
          </div>
        ))}
      </dl>

      <p className="text-caption text-slate">{orderingSentence(direction)}</p>

      {referenceMissing && (
        <p className="text-caption text-warning">
          No regulatory value is governed for this metric, so its capacity is{" "}
          {NO_REGULATORY_FLOOR}.
        </p>
      )}

      {ordering.violations.length > 0 && (
        <ul className="space-y-0.5 text-caption text-critical">
          {ordering.violations.map((violation) => (
            <li key={violation.code}>{violation.message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
