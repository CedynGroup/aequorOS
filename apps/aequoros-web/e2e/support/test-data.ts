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

export const northstarCase = {
  id: "90000000-0000-4000-8000-000000000001",
  title: "Covenant review - Northstar Foods",
} as const satisfies DemoCase;
