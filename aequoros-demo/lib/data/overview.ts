/** Overview dashboard — cross-module health for the home screen. */

export const overviewKpis = [
  {
    label: 'Capital Adequacy Ratio',
    value: 14.2,
    suffix: '%',
    threshold: 10,
    decimals: 2,
    status: 'compliant' as const,
    delta: +0.4,
    sublabel: 'BoG minimum 10.0%',
    href: '/basel',
    sparkline: [13.0, 13.2, 13.5, 13.6, 13.8, 13.9, 14.1, 14.0, 14.1, 14.2],
  },
  {
    label: 'Liquidity Coverage Ratio',
    value: 142.0,
    suffix: '%',
    threshold: 100,
    decimals: 1,
    status: 'compliant' as const,
    delta: +3.5,
    sublabel: 'BoG minimum 100.0%',
    href: '/liquidity',
    sparkline: [128.4, 131.2, 129.8, 134.5, 136.7, 134.1, 137.2, 139.5, 141.0, 142.0],
  },
  {
    label: 'Net Stable Funding Ratio',
    value: 118.0,
    suffix: '%',
    threshold: 100,
    decimals: 1,
    status: 'compliant' as const,
    delta: +1.8,
    sublabel: 'BoG minimum 100.0%',
    href: '/liquidity/nsfr',
    sparkline: [114.2, 114.8, 115.1, 115.4, 116.0, 115.8, 116.2, 117.0, 117.6, 118.0],
  },
  {
    label: 'Net Open FX Position',
    value: 4.8,
    suffix: '%',
    threshold: 5,
    decimals: 1,
    status: 'approaching' as const,
    delta: +0.6,
    sublabel: 'BoG limit 5.0% of capital',
    href: '/fx',
    sparkline: [3.9, 4.0, 4.2, 4.1, 4.3, 4.4, 4.5, 4.5, 4.7, 4.8],
  },
];

export const upcomingDeadlines = [
  {
    title: 'BoG Monthly Prudential Return',
    form: 'BSD-2',
    dueDate: '10 Apr 2026',
    status: 'In review',
    severity: 'amber',
    action: 'Review submission',
  },
  {
    title: 'BoG LCR Monthly Submission',
    form: 'LCR-1',
    dueDate: '10 Apr 2026',
    status: 'Ready to submit',
    severity: 'success',
    action: 'Generate filing',
  },
  {
    title: 'Capital Adequacy Return — Q1 2026',
    form: 'CAR-Q',
    dueDate: '15 Apr 2026',
    status: 'Pending data',
    severity: 'amber',
    action: 'Resolve gaps',
  },
  {
    title: 'ICAAP Internal Submission — H1',
    form: 'ICAAP',
    dueDate: '30 Jun 2026',
    status: 'Drafting',
    severity: 'slate',
    action: 'Continue draft',
  },
];

export const recentActivity = [
  {
    when: '11:42',
    actor: 'Akua Mensah',
    event: 'Approved Q1 ALCO liquidity report',
    module: 'Liquidity',
  },
  {
    when: '10:18',
    actor: 'AI Hedging',
    event: 'Generated 3 hedge recommendations for IRR exposure',
    module: 'IRR',
  },
  {
    when: '09:55',
    actor: 'Kojo Aboagye',
    event: 'Updated FTP curve with 28-Mar BoG T-bill auction rates',
    module: 'FTP',
  },
  {
    when: '09:32',
    actor: 'System',
    event: 'Daily LCR recalculation completed (run 142.2%, prior 138.5%)',
    module: 'Liquidity',
  },
  {
    when: 'Yesterday',
    actor: 'Yaa Adjei',
    event: 'Submitted Q1 stress test results to Risk Committee',
    module: 'Basel',
  },
];

export const aiInsights = [
  {
    module: 'Liquidity',
    title: 'LSTM forecast indicates 24-day liquidity surplus',
    body: 'Projected net inflows of GHS 18.2M over next 30 days driven by wholesale repayment cycle. Consider deploying surplus into 91-day T-bill auction settling 8 Apr.',
    severity: 'success',
    confidence: 0.86,
  },
  {
    module: 'IRR',
    title: 'EVE sensitivity to +200bps shock approaching policy limit',
    body: 'Current EVE/Tier 1 sensitivity = 11.3% vs 15% policy cap. Deep RL recommends adding 6M IRS notional GHS 50M, pay fixed.',
    severity: 'amber',
    confidence: 0.81,
  },
  {
    module: 'FX',
    title: 'GHS/USD ML forecast diverging from forward implieds',
    body: 'Ensemble model 30-day forecast: 12.94 vs forward implied 12.71. Net long USD position would benefit; review hedge ratio.',
    severity: 'amber',
    confidence: 0.74,
  },
];
