/**
 * Guided-tour step definitions. Each step anchors on a stable shell selector
 * (nav hrefs and aria-labels from Sidebar/Header — those files are never
 * edited by the tour). `selectors` are tried in order; the first visible
 * match wins. A step with no visible match renders its card centered with
 * the full-screen dim, so a collapsed sidebar or missing freshness pill
 * never breaks the walkthrough.
 */

export type TourStep = {
  id: string;
  title: string;
  body: string;
  /** Candidate CSS selectors, first visible match wins. Empty = centered. */
  selectors: string[];
};

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    title: 'Welcome to AequorOS',
    body: 'This is the Command Center — a live cross-module view of your treasury and risk position. Everything you see is computed server-side by deterministic engines and updates as data lands.',
    selectors: ["aside nav a[href='/']"],
  },
  {
    id: 'module-rail',
    title: 'The module rail',
    body: 'Every regulatory module lives here: IRRBB, Liquidity, FX, Basel Capital, FTP, Forecasting, and Behavioral — plus the Data Engine and the governance pages (Reports, Institution Profile, Regulatory Reporting, Settings).',
    selectors: ['aside nav'],
  },
  {
    id: 'risk',
    title: 'Risk & Limits',
    body: 'A single limits board across modules: each regulatory ratio against its central-bank threshold, with traffic-light status and headroom.',
    selectors: ["aside nav a[href='/risk']"],
  },
  {
    id: 'markets',
    title: 'Markets',
    body: 'Market data that feeds the engines — policy rate, T-bill curve, FX rates — with vendor connections managed in the Data Engine.',
    selectors: ["aside nav a[href='/markets']"],
  },
  {
    id: 'data-engine',
    title: 'Data Engine',
    body: 'Push data by API or upload Excel/CSV — ingestion derives facts and recomputes every module automatically in the background. No manual recalculation step.',
    selectors: ["aside nav a[href='/data-engine']"],
  },
  {
    id: 'freshness',
    title: 'Live freshness',
    body: 'This pill tracks live figures against the last finalised results. Green means in sync; "Changed" means data moved since the period was last closed — reconcile under Governance → Reports.',
    selectors: [
      "header *[title^='Live figures']",
      "header *[title^='Data has changed']",
    ],
  },
  {
    id: 'alerts',
    title: 'Breach alerts',
    body: 'Open limit breaches across all modules land here the moment the live engine detects them, each linking straight to the offending dashboard.',
    selectors: ["header button[aria-label^='Alerts']"],
  },
  {
    id: 'palette',
    title: 'Command palette',
    body: 'Press ⌘K (or Ctrl+K) anywhere to jump between modules, periods, and actions without touching the mouse. That is the tour — explore freely.',
    selectors: [
      'header button:has(kbd)',
      "header button[aria-label='Search']",
    ],
  },
];
