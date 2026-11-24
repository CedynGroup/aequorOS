import type { GatewayInstitution, GatewayInstitutionType } from "@/lib/api";

export const INSTITUTION_TYPES: { value: GatewayInstitutionType; label: string }[] = [
  { value: "universal_bank", label: "Universal bank" },
  { value: "rural_bank", label: "Rural and community bank" },
  { value: "savings_and_loans", label: "Savings and loans" },
  { value: "microfinance", label: "Microfinance institution" },
  { value: "other", label: "Other" },
];

export function institutionTypeLabel(value: GatewayInstitutionType): string {
  return INSTITUTION_TYPES.find((t) => t.value === value)?.label ?? value;
}

export const STATUS_LABEL: Record<GatewayInstitution["status"], string> = {
  enrolled: "Enrolled",
  live: "Live",
  suspended: "Suspended",
};

export const STATUS_TONE: Record<GatewayInstitution["status"], "neutral" | "ok" | "warn"> = {
  enrolled: "neutral",
  live: "ok",
  suspended: "warn",
};

export const ENVIRONMENT_LABEL = { sandbox: "Sandbox", production: "Production" } as const;

/** A pasted public key as a JWK object, or an explanation of what is wrong with it. */
export function parsePublicJwk(text: string): Record<string, string> | string {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return "Paste the public key as JSON (a JWK).";
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return "Paste a single JWK object.";
  }
  const jwk = value as Record<string, unknown>;
  if ("d" in jwk) return "This is a private key. Ask the institution for its public key only.";
  if (jwk.kty !== "EC" || jwk.crv !== "P-256" || typeof jwk.x !== "string" || typeof jwk.y !== "string") {
    return "The key must be an EC P-256 public key (kty EC, crv P-256, x and y).";
  }
  const out: Record<string, string> = {};
  for (const [key, field] of Object.entries(jwk)) {
    if (typeof field === "string") out[key] = field;
  }
  return out;
}
