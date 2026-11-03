import { currencyCode, fmtNum, fmtLocale } from "@/lib/format";

const ACRONYMS = new Set([
  "lcr",
  "nsfr",
  "asf",
  "rsf",
  "hqla",
  "car",
  "cet1",
  "rwa",
  "ecl",
  "ftp",
  "nim",
  "nmd",
  "fx",
  "nop",
  "irrbb",
]);

const CURRENCIES = new Set(
  Intl.supportedValuesOf("currency").map((code) => code.toLowerCase()),
);

export function scenarioLabel(key: string): string {
  return key
    .replace(/_ghs$/, "")
    .replace(/_pct(?=_|$)/g, " percentage ")
    .replace(/_bps?$/, " bps")
    .split(/[_: ]+/)
    .filter(Boolean)
    .map((word, i) =>
      ACRONYMS.has(word.toLowerCase()) || CURRENCIES.has(word.toLowerCase())
        ? word.toUpperCase()
        : i === 0
          ? word.charAt(0).toUpperCase() + word.slice(1)
          : word.toLowerCase(),
    )
    .join(" ");
}

const MONEY_FIELDS = new Set([
  "cet1_capital",
  "tier1_capital",
  "total_capital",
  "credit_rwa",
  "market_rwa",
  "operational_rwa",
  "total_rwa",
  "assets",
  "liabilities",
  "net",
  "cumulative",
  "assets_total",
  "liabilities_total",
  "net_total",
  "stressed_liabilities_total",
  "stressed_net_total",
  "contractual_liabilities",
  "stressed_liabilities",
  "stressed_net",
  "stressed_cumulative",
  "demand_deposits",
  "stable_core",
]);

function unit(key: string): "money" | "percent" | "bps" | undefined {
  if (key.endsWith("_ghs") || MONEY_FIELDS.has(key)) return "money";
  if (
    /(^|_)pct($|_)/.test(key) ||
    ["car", "cet1_ratio", "tier1_ratio", "leverage_ratio"].includes(key)
  )
    return "percent";
  if (/_bps?$/.test(key)) return "bps";
}

export function scenarioValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "")
    return "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const numeric =
    typeof value === "number" ||
    (typeof value === "string" &&
      /^[-+]?\d+(\.\d*)?([eE][-+]?\d+)?$/.test(value));
  if (!numeric) return String(value).replaceAll("—", ".");
  const number = Number(value);
  if (!Number.isFinite(number)) return "Unavailable";
  const kind = unit(key);
  const formatted =
    kind === "money" || kind === "percent"
      ? fmtNum(number, 2)
      : number.toLocaleString(fmtLocale(), { maximumFractionDigits: 4 });
  return kind === "money"
    ? `${currencyCode()} ${formatted}`
    : kind === "percent"
      ? `${formatted}%`
      : kind === "bps"
        ? `${formatted} bps`
        : formatted;
}

export function scenarioDelta(
  key: string,
  value: unknown,
  baseline: unknown,
): string {
  if (
    value === null ||
    baseline === null ||
    value === undefined ||
    baseline === undefined ||
    value === "" ||
    baseline === "" ||
    typeof value === "boolean" ||
    typeof baseline === "boolean"
  )
    return "Not comparable";
  const changed = Number(value),
    original = Number(baseline);
  if (!Number.isFinite(changed) || !Number.isFinite(original))
    return "Not comparable";
  const delta = changed - original;
  const formatted =
    unit(key) === "percent"
      ? `${fmtNum(delta, 2)} pp`
      : scenarioValue(key, delta);
  return delta > 0 ? `+${formatted}` : formatted;
}

export function scenarioRisk(
  metrics: Record<string, unknown>,
  statuses: Record<string, string> = {},
): string {
  const findings: string[] = [];
  let assessed = false;
  for (const [key, value] of Object.entries(metrics)) {
    if (
      (key.startsWith("within_") ||
        key.endsWith("_within_policy") ||
        key === "breached") &&
      typeof value === "boolean"
    ) {
      assessed = true;
      if (key === "breached" ? value : !value)
        findings.push(`${scenarioLabel(key)}: ${value ? "Yes" : "No"}`);
    }
    if (
      key === "products_below_min_margin" &&
      value !== null &&
      value !== undefined &&
      Number.isFinite(Number(value))
    ) {
      assessed = true;
      if (Number(value) > 0)
        findings.push(`${value} products below the margin floor`);
    }
    if (key === "triggers" && Array.isArray(value)) {
      for (const trigger of value) {
        if (
          trigger &&
          typeof trigger === "object" &&
          "fired" in trigger &&
          typeof trigger.fired === "boolean"
        ) {
          assessed = true;
          if (trigger.fired)
            findings.push(
              "action" in trigger
                ? String(trigger.action)
                : "Capital trigger breached",
            );
        }
      }
    }
  }
  for (const [key, status] of Object.entries(statuses)) {
    if (
      ["red", "breach", "breached", "amber", "green", "compliant"].includes(
        status,
      )
    )
      assessed = true;
    if (["red", "breach", "breached"].includes(status))
      findings.push(scenarioLabel(key));
  }
  return findings.length
    ? `Risk breaches: ${findings.join("; ")}.`
    : assessed
      ? "No reported breaches. Review warnings and policy limits."
      : "Risk assessment unavailable. Review the stored metrics and governed limits.";
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function StructuredValue({ name, value }: { name: string; value: unknown }) {
  if (Array.isArray(value)) {
    if (!value.length) return <p>No entries recorded.</p>;
    if (value.every(record)) {
      const keys = [...new Set(value.flatMap((row) => Object.keys(row)))];
      return (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-caption">
            <caption className="sr-only">{scenarioLabel(name)}</caption>
            <thead>
              <tr>
                {keys.map((key) => (
                  <th
                    scope="col"
                    className="px-3 py-2 text-slate font-medium"
                    key={key}
                  >
                    {scenarioLabel(key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.map((row, i) => (
                <tr key={i} className="border-t border-border-light">
                  {keys.map((key) => (
                    <td key={key} className="px-3 py-2 align-top">
                      <StructuredValue name={key} value={row[key]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return (
      <ol className="list-decimal pl-5">
        {value.map((entry, i) => (
          <li key={i}>
            <StructuredValue name={name} value={entry} />
          </li>
        ))}
      </ol>
    );
  }
  if (record(value)) return <ScenarioMetrics metrics={value} />;
  return <span>{scenarioValue(name, value)}</span>;
}

export default function ScenarioMetrics({
  metrics,
  baseline,
  statuses = {},
  refusals = {},
}: {
  metrics: Record<string, unknown>;
  baseline?: Record<string, unknown>;
  statuses?: Record<string, string>;
  refusals?: Record<string, { reason?: string | null; reasonCode?: string }>;
}) {
  const entries = Object.entries(metrics);
  const scalar = entries.filter(
    ([, value]) => value === null || typeof value !== "object",
  );
  const structured = entries.filter(
    ([, value]) => value !== null && typeof value === "object",
  );
  return (
    <div className="space-y-4">
      {scalar.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-caption">
            <caption className="sr-only">Scenario metrics</caption>
            <thead>
              <tr>
                <th scope="col" className="p-2">
                  Metric
                </th>
                <th scope="col" className="p-2">
                  Value
                </th>
                {baseline && (
                  <th scope="col" className="p-2">
                    Change from baseline
                  </th>
                )}
                <th scope="col" className="p-2">
                  Assessment
                </th>
              </tr>
            </thead>
            <tbody>
              {scalar.map(([key, value]) => (
                <tr key={key} className="border-t border-border-light">
                  <th scope="row" className="p-2 font-medium text-navy">
                    {scenarioLabel(key)}
                  </th>
                  <td className="p-2 font-mono">
                    {refusals[key]
                      ? `Unavailable: ${refusals[key].reason ?? scenarioLabel(refusals[key].reasonCode ?? "not_assessed")}`
                      : scenarioValue(key, value)}
                  </td>
                  {baseline && (
                    <td className="p-2 font-mono">
                      {refusals[key]
                        ? "Not comparable"
                        : scenarioDelta(key, value, baseline[key])}
                    </td>
                  )}
                  <td className="p-2">
                    {statuses[key] === "red" ? (
                      <span className="text-critical font-medium">
                        Policy breach. Outside the configured limit.
                      </span>
                    ) : statuses[key] === "amber" ? (
                      "Warning. Approaching the configured limit."
                    ) : statuses[key] ? (
                      scenarioLabel(statuses[key])
                    ) : (
                      "Not separately assessed"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {structured.map(([key, value]) =>
        key === "basis" ? (
          <details key={key}>
            <summary className="cursor-pointer text-slate">
              Calculation basis and evidence
            </summary>
            <StructuredValue name={key} value={value} />
          </details>
        ) : (
          <section key={key} className="space-y-2">
            <h4 className="font-medium">{scenarioLabel(key)}</h4>
            <StructuredValue name={key} value={value} />
          </section>
        ),
      )}
      {entries.length === 0 && <p>No metrics recorded.</p>}
    </div>
  );
}
