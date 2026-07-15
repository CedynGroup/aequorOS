export const DEFAULT_ORG_ID = "11111111-1111-4111-8111-111111111111";
export const DEFAULT_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8003/api/v1";

export type TenantOption = {
  name: string;
  orgId: string;
  userId: string;
};
export type TenantDirectory = readonly [TenantOption, ...TenantOption[]];

const defaultTenantOptions: TenantDirectory = [
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

export function tenantOptions(): TenantDirectory {
  const configured = import.meta.env.VITE_RISK_TENANTS;
  if (!configured) return defaultTenantOptions;

  try {
    const parsed: unknown = JSON.parse(configured);
    if (
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      parsed.every(isTenantOption)
    ) {
      return parsed as unknown as TenantDirectory;
    }
  } catch {
    return defaultTenantOptions;
  }

  return defaultTenantOptions;
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
    candidate.orgId.trim() !== "" &&
    typeof candidate.userId === "string" &&
    candidate.userId.trim() !== ""
  );
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
