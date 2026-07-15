export const DEFAULT_ORG_ID = "11111111-1111-4111-8111-111111111111";
export const DEFAULT_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8003/api/v1";

export type TenantOption = {
  name: string;
  orgId: string;
  userId: string;
};
export type TenantDirectory = readonly [TenantOption, ...TenantOption[]];
export type TenantConfiguration =
  | { status: "ready"; tenants: TenantDirectory }
  | { status: "error"; error: string };

export const DEFAULT_TENANT_OPTIONS: TenantDirectory = [
  {
    name: "AequorOS Demo Organization",
    orgId: DEFAULT_ORG_ID,
    userId: DEFAULT_USER_ID,
  },
  {
    name: "AequorOS Isolated Tenant",
    orgId: "22222222-2222-4222-8222-222222222222",
    userId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  },
];

export function tenantConfiguration(): TenantConfiguration {
  const configured = import.meta.env.VITE_RISK_TENANTS;
  if (configured === undefined)
    return { status: "ready", tenants: DEFAULT_TENANT_OPTIONS };

  let parsed: unknown;
  try {
    parsed = JSON.parse(configured);
  } catch {
    return {
      status: "error",
      error: "VITE_RISK_TENANTS must contain valid JSON.",
    };
  }

  if (!Array.isArray(parsed) || parsed.length === 0) {
    return {
      status: "error",
      error: "VITE_RISK_TENANTS must be a non-empty JSON array.",
    };
  }

  const invalidIndex = parsed.findIndex((value) => !isTenantOption(value));
  if (invalidIndex !== -1) {
    return {
      status: "error",
      error: `VITE_RISK_TENANTS entry ${invalidIndex + 1} must contain a non-empty name and valid UUID orgId and userId fields.`,
    };
  }

  const tenants = parsed.map(
    normalizeTenantOption,
  ) as unknown as TenantDirectory;
  if (new Set(tenants.map((tenant) => tenant.orgId)).size !== tenants.length) {
    return {
      status: "error",
      error: "VITE_RISK_TENANTS must not contain duplicate orgId values.",
    };
  }

  return { status: "ready", tenants };
}

export function activeTenantOption(
  tenants: TenantDirectory,
  orgId: string,
): TenantOption {
  return tenants.find((tenant) => tenant.orgId === orgId) ?? tenants[0];
}

function isTenantOption(value: unknown): value is TenantOption {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<TenantOption>;
  return (
    typeof candidate.name === "string" &&
    candidate.name.trim() !== "" &&
    typeof candidate.orgId === "string" &&
    isUuid(candidate.orgId) &&
    typeof candidate.userId === "string" &&
    isUuid(candidate.userId)
  );
}

function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value.trim(),
    )
  );
}

function normalizeTenantOption(tenant: TenantOption): TenantOption {
  return {
    name: tenant.name.trim(),
    orgId: tenant.orgId.trim().toLowerCase(),
    userId: tenant.userId.trim().toLowerCase(),
  };
}

export const tabs = [
  "overview",
  "financial",
  "scenarios",
  "calculations",
  "capital",
  "liquidity",
  "findings",
  "decisions",
  "documents",
  "report",
] as const;

export type ConsoleTab = (typeof tabs)[number];
export type ReportMode = "json" | "html";

export function isConsoleTab(value: string | null): value is ConsoleTab {
  return tabs.includes(value as ConsoleTab);
}

export function apiBaseUrl() {
  return (
    import.meta.env.VITE_RISK_API_BASE_URL?.replace(/\/$/, "") ??
    DEFAULT_API_BASE_URL
  );
}
