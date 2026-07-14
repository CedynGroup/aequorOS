import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function labelize(value: string | null | undefined) {
  if (!value) return "None";
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function truncateId(value: string | null | undefined) {
  if (!value) return "Unassigned";
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

export function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export function formatMoney(value: string, currency: string) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return `${currency} ${value}`;

  let formatter: Intl.NumberFormat;
  try {
    formatter = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    });
  } catch (error) {
    if (error instanceof RangeError) return `${currency} ${value}`;
    throw error;
  }
  const resolvedOptions = formatter.resolvedOptions();
  const minimumFractionDigits = resolvedOptions.minimumFractionDigits ?? 0;
  const maximumFractionDigits = resolvedOptions.maximumFractionDigits ?? 2;
  const rounded = roundDecimal(match[2], match[3] ?? "", maximumFractionDigits);
  const groupedInteger = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
  }).format(BigInt(rounded.integer));
  const decimalSeparator =
    formatter.formatToParts(0.1).find((part) => part.type === "decimal")
      ?.value ?? ".";
  const fraction = rounded.fraction.padEnd(minimumFractionDigits, "0");
  const formattedNumber = `${groupedInteger}${fraction ? decimalSeparator + fraction : ""}`;
  const parts = formatter.formatToParts(match[1] ? -0 : 0);
  let insertedNumber = false;

  return parts
    .map((part) => {
      if (["integer", "group", "decimal", "fraction"].includes(part.type)) {
        if (insertedNumber) return "";
        insertedNumber = true;
        return formattedNumber;
      }
      return part.value;
    })
    .join("");
}

function roundDecimal(integer: string, fraction: string, digits: number) {
  const keptFraction = fraction.slice(0, digits).padEnd(digits, "0");
  const shouldRoundUp = Number(fraction[digits] ?? "0") >= 5;
  const scale = 10n ** BigInt(digits);
  const scaled = BigInt(integer) * scale + BigInt(keptFraction || "0");
  const rounded = scaled + (shouldRoundUp ? 1n : 0n);
  const roundedInteger = rounded / scale;
  const roundedFraction =
    digits > 0 ? (rounded % scale).toString().padStart(digits, "0") : "";
  return { integer: roundedInteger.toString(), fraction: roundedFraction };
}
