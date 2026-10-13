/** Net CRD exposures from tests/support/factories/canonical.py, GHS millions. */
export const CREDIT_LOANS = [
  // Domestic corporate 30 + 10, plus the ingested USD conversion 12.85.
  { label: "Corporate Unrated", exposure: 30 + 10 + 12.85, rw: 100 },
  { label: "Sme Retail", exposure: 9, rw: 100 },
  { label: "Retail Other", exposure: 8, rw: 75 },
  { label: "Residential Mortgage", exposure: 12, rw: 35 },
  // Stage-3 specific allowance nets down the 3M past-due balance.
  { label: "Past Due 90", exposure: 3 - 0.9, rw: 150 },
];

/** Residual source assets do not grow with forecast loan balances. */
export const CREDIT_RESIDUALS = [
  { label: "Interbank", exposure: 5, rw: 50 },
  { label: "Other Assets", exposure: 7, rw: 100 },
];

/** Commitments remain the aggregate spine's independently checked positions. */
const CREDIT_COMMITMENTS = [
  {
    label: "Committed Corporate (EAD After 50% CCF)",
    exposure: 240 * 0.5,
    rw: 100,
  },
  { label: "Committed Retail (EAD After 50% CCF)", exposure: 80 * 0.5, rw: 75 },
];

export const CREDIT = [
  ...CREDIT_LOANS,
  ...CREDIT_RESIDUALS,
  ...CREDIT_COMMITMENTS,
  { label: "Securities:Domestic Sovereign", exposure: 20, rw: 0 },
  { label: "Securities:Domestic Sovereign:GoG", exposure: 15, rw: 0 },
  { label: "Cash Vault", exposure: 5, rw: 0 },
  { label: "BoG Required Reserves", exposure: 8, rw: 0 },
  { label: "BoG Excess Reserves", exposure: 4, rw: 0 },
];

export const CREDIT_RWA_M = CREDIT.reduce(
  (total, item) => total + (item.exposure * item.rw) / 100,
  0,
);
