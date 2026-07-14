export type Uuid = `${string}-${string}-${string}-${string}-${string}`;

export type DemoTenant = {
  orgId: Uuid;
  userId: Uuid;
};

export type DemoCase = {
  id: Uuid;
  title: string;
};

export const demoTenant = {
  orgId: "11111111-1111-4111-8111-111111111111",
  userId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
} as const satisfies DemoTenant;

export const breachingCase = {
  id: "90000000-0000-4000-8000-000000000002",
  title: "Covenant exception — Adom Textiles & Garments Ltd",
} as const satisfies DemoCase;

export const liquidityCase = {
  id: "90000000-0000-4000-8000-000000000003",
  title: "Liquidity stress review — Kivu Fresh Produce Logistics Ltd",
} as const satisfies DemoCase;

export const completedCase = {
  id: "90000000-0000-4000-8000-000000000004",
  title: "Completed review — Baobab Health Distribution SA",
} as const satisfies DemoCase;

// Existing journey helpers use this name; keep the alias while the seeded
// narrative now centers its mutable review flows on Adom Textiles.
export const northstarCase = breachingCase;
