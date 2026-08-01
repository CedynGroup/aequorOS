/**
 * Public product UI captures used on the marketing site.
 * Source masters live in docs/collateral/screenshots/ — keep filenames aligned.
 *
 * All present-tense captions describe shipped product surface, not roadmap.
 */
export type ProductScreen = {
  id: string;
  src: `/images/product/${string}`;
  title: string;
  caption: string;
  alt: string;
  /** Short label for thumbnails / chips */
  label: string;
};

export const productScreens: ProductScreen[] = [
  {
    id: 'command-center',
    src: '/images/product/command-center.png',
    title: 'Command Center',
    label: 'Command Center',
    caption:
      'Live module pulse across liquidity, capital, IRR, FX, FTP, and forecasting — with freshness and breach context on one screen.',
    alt: 'AequorOS Command Center dashboard showing live module status cards for Treasury and Risk engines',
  },
  {
    id: 'data-engine',
    src: '/images/product/data-engine.png',
    title: 'Data Engine',
    label: 'Data Engine',
    caption:
      'Connect core systems, watch ingestion health, and land every accepted load in an auditable canonical model.',
    alt: 'AequorOS Data Engine overview with connection health and recent ingestion batches',
  },
  {
    id: 'liquidity',
    src: '/images/product/liquidity.png',
    title: 'Liquidity',
    label: 'Liquidity',
    caption:
      'LCR and NSFR computed from the book — HQLA, outflows, inflows, and stable-funding views on the same engine.',
    alt: 'AequorOS Liquidity module showing LCR ratio, component breakdown, and funding metrics',
  },
  {
    id: 'basel',
    src: '/images/product/basel.png',
    title: 'Regulatory Capital',
    label: 'Capital',
    caption:
      'Basel standardized capital stack: RWA composition, CET1 / Tier 1 / CAR, and headroom against regulatory floors.',
    alt: 'AequorOS Basel capital dashboard with CAR gauge, capital waterfall, and RWA composition',
  },
  {
    id: 'submissions',
    src: '/images/product/submissions.png',
    title: 'Regulatory Submissions',
    label: 'Submissions',
    caption:
      'Bank of Ghana BSD returns generated from the platform — immutable runs, export-ready, examiner-reproducible.',
    alt: 'AequorOS regulatory submissions workspace with Bank of Ghana return packages',
  },
  {
    id: 'irr',
    src: '/images/product/irr.png',
    title: 'Interest Rate Risk',
    label: 'IRRBB',
    caption:
      'Repricing gap, duration, and EVE across the Basel IRRBB scenario set — deterministic and fully auditable.',
    alt: 'AequorOS interest-rate risk overview with repricing gap chart and EVE scenario grid',
  },
  {
    id: 'irr-scenarios',
    src: '/images/product/irr-scenarios.png',
    title: 'IRRBB Scenarios',
    label: 'IRR scenarios',
    caption:
      'Full Basel scenario set with ΔEVE monitored against the Tier 1 outlier threshold.',
    alt: 'AequorOS IRRBB scenario results comparing economic value of equity under rate shocks',
  },
  {
    id: 'fx',
    src: '/images/product/fx.png',
    title: 'FX Risk',
    label: 'FX',
    caption:
      'Net open position by currency against limits, with VaR and hedge-effectiveness views for regional pairs.',
    alt: 'AequorOS FX risk module showing net open position by currency versus regulatory limits',
  },
  {
    id: 'ftp',
    src: '/images/product/ftp.png',
    title: 'Funds Transfer Pricing',
    label: 'FTP',
    caption:
      'Matched-maturity FTP curves and product-level profitability — ALCO-grade transfer pricing, not folklore spreads.',
    alt: 'AequorOS funds transfer pricing dashboard with FTP curve and product profitability',
  },
  {
    id: 'forecasting-whatif',
    src: '/images/product/forecasting-whatif.png',
    title: 'Balance-Sheet What-If',
    label: 'Forecasting',
    caption:
      'Shock the book and re-run the real engines — base vs stressed paths with breach flags in seconds.',
    alt: 'AequorOS forecasting what-if lab comparing base and shocked balance-sheet projection paths',
  },
  {
    id: 'positions-lineage',
    src: '/images/product/positions-lineage.png',
    title: 'Positions & Lineage',
    label: 'Lineage',
    caption:
      'Every figure traces back to source input, batch, and timestamp — the thing a bank examiner actually asks for.',
    alt: 'AequorOS positions view with end-to-end data lineage from source to calculated metric',
  },
  {
    id: 'behavioral',
    src: '/images/product/behavioral.png',
    title: 'Behavioral Models',
    label: 'Behavioral',
    caption:
      'Per-institution behavioral assumptions for non-maturity deposits and prepayment — reviewed, versioned, and auditable.',
    alt: 'AequorOS behavioral modeling workspace for deposit and prepayment assumptions',
  },
];

/** Hero + homepage strip — highest-signal surfaces for a bank conversation. */
export const heroScreen = productScreens[0];

export const homepageFeatureScreens = productScreens.filter((s) =>
  ['command-center', 'data-engine', 'liquidity', 'submissions'].includes(s.id),
);

/** Product page primary walkthrough order. */
export const productWalkthroughIds = [
  'command-center',
  'data-engine',
  'liquidity',
  'basel',
  'submissions',
  'irr',
  'fx',
  'ftp',
  'forecasting-whatif',
  'positions-lineage',
] as const;

export function screenById(id: string): ProductScreen | undefined {
  return productScreens.find((s) => s.id === id);
}
