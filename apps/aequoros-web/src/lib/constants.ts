export const DEFAULT_ORG_ID = "11111111-1111-4111-8111-111111111111";
export const DEFAULT_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8003/api/v1";

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
export type ConsoleMode = "cases" | "alm";

export function isConsoleTab(value: string | null): value is ConsoleTab {
  return tabs.includes(value as ConsoleTab);
}

export const almTabs = [
  "overview",
  "lcr",
  "nsfr",
  "liq-stress",
  "cashflow",
  "capital",
  "rwa",
  "structure",
  "capital-stress",
  "forecast",
  "optimizer",
  "whatif",
  "submissions",
] as const;

export type AlmTab = (typeof almTabs)[number];

export function isAlmTab(value: string | null): value is AlmTab {
  return almTabs.includes(value as AlmTab);
}

export function apiBaseUrl() {
  return (
    import.meta.env.VITE_RISK_API_BASE_URL?.replace(/\/$/, "") ??
    DEFAULT_API_BASE_URL
  );
}
