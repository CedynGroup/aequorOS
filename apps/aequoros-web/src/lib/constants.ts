export const DEFAULT_ORG_ID = "11111111-1111-4111-8111-111111111111";
export const DEFAULT_USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8003/api/v1";

export const tabs = [
  "overview",
  "financial",
  "scenarios",
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
