import { formatDecimal, formatMoney } from "../../../lib/money";

export type RatioStatus = "green" | "amber" | "red" | "na";

export function statusTone(
  status: string,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "green") return "success";
  if (status === "amber") return "warning";
  if (status === "red") return "danger";
  return "neutral";
}

export function severityTone(
  severity: string,
): "danger" | "warning" | "info" | "neutral" {
  if (severity === "error") return "danger";
  if (severity === "warning") return "warning";
  if (severity === "info") return "info";
  return "neutral";
}

export function runStatusTone(
  status: string,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "succeeded") return "success";
  if (status === "failed") return "danger";
  if (status === "running" || status === "queued") return "warning";
  return "neutral";
}

export function formatPct(value: string | null | undefined, digits = 1) {
  if (value === null || value === undefined) return "n/a";
  return `${formatDecimal(value, digits)}%`;
}

export function formatPp(value: number, digits = 1) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)} pp`;
}

export function formatMoneyCompact(
  value: string | null | undefined,
  currency: string,
) {
  if (value === null || value === undefined) return "n/a";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return `${currency} ${value}`;
  const abs = Math.abs(numeric);
  let scaled = numeric;
  let suffix = "";
  if (abs >= 1e9) {
    scaled = numeric / 1e9;
    suffix = "B";
  } else if (abs >= 1e6) {
    scaled = numeric / 1e6;
    suffix = "M";
  } else if (abs >= 1e3) {
    scaled = numeric / 1e3;
    suffix = "K";
  }
  const digits = suffix && Math.abs(scaled) < 100 ? 2 : suffix ? 1 : 2;
  return `${currency} ${scaled.toFixed(digits)}${suffix}`;
}

export function formatMoneyFull(
  value: string | null | undefined,
  currency: string,
) {
  if (value === null || value === undefined) return "n/a";
  return formatMoney(value, currency);
}

export function formatDate(value: Date) {
  return value.toLocaleDateString();
}

export function formatDateTime(value: Date) {
  return value.toLocaleString();
}
