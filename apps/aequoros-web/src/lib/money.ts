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
  return formatDecimalParts(match, formatter);
}

export function formatDecimal(value: string, fractionDigits: number) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;

  return formatDecimalParts(
    match,
    new Intl.NumberFormat(undefined, {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }),
  );
}

export function formatPercent(
  value: string | number,
  maximumFractionDigits = 2,
) {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
  if (!match) return String(value);
  const scaledMatch = /^(-?)(\d+)(?:\.(\d+))?$/.exec(
    multiplyDecimalBy100(match),
  );
  if (!scaledMatch) return String(value);
  return formatDecimalParts(
    scaledMatch,
    new Intl.NumberFormat(undefined, {
      style: "percent",
      maximumFractionDigits,
    }),
    true,
  );
}

function multiplyDecimalBy100(match: RegExpExecArray) {
  const integer = match[2];
  const fraction = match[3] ?? "";
  const digits = `${integer}${fraction.padEnd(2, "0")}`;
  const decimalIndex = integer.length + 2;
  const scaledInteger = digits.slice(0, decimalIndex).replace(/^0+(?=\d)/, "");
  const scaledFraction = digits.slice(decimalIndex).replace(/0+$/, "");
  return `${match[1]}${scaledInteger}${scaledFraction ? `.${scaledFraction}` : ""}`;
}

function formatDecimalParts(
  match: RegExpExecArray,
  formatter: Intl.NumberFormat,
  trimTrailingZeros = false,
) {
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
  const fraction = (
    trimTrailingZeros ? rounded.fraction.replace(/0+$/, "") : rounded.fraction
  ).padEnd(minimumFractionDigits, "0");
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
