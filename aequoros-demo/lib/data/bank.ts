/**
 * Sample Bank Limited — synthetic mid-tier Ghanaian universal bank.
 * All figures are illustrative. Numbers anchored to the AequorOS Figma Design Brief.
 */

export const bank = {
  name: 'Sample Bank Limited',
  shortName: 'Sample Bank',
  founded: 2005,
  jurisdiction: 'Ghana',
  regulator: 'Bank of Ghana',
  licenseClass: 'Universal Bank',
  asOf: '31 March 2026',

  // Balance sheet
  totalAssetsGHS: 2_400_000_000, // GHS 2.4B
  totalDepositsGHS: 1_900_000_000, // GHS 1.9B
  totalLoansGHS: 1_400_000_000, // GHS 1.4B
  capitalBaseGHS: 230_000_000, // Tier 1 + Tier 2

  // Operations
  branches: 18,
  customers: 85_000,
  employees: 420,

  // Reference rates
  ghsUsd: 12.5,
  ngnUsd: 1500,
  bogPolicyRate: 28.0, // %
  bogTbillRate91d: 25.4, // %
};

export const treasurer = {
  name: 'Akua Mensah',
  role: 'Head of Treasury & ALM',
  initials: 'AM',
};
