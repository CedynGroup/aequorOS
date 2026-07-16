'use client';

import BehavioralModelPage from '@/components/behavioral/BehavioralModelPage';

export default function PrepaymentPage() {
  return (
    <BehavioralModelPage
      slug="prepayment"
      config={{
        title: 'Loan Prepayment',
        subtitle:
          "Annual conditional prepayment rate (CPR) per loan product, learned from realized unscheduled principal · feeds LCR & cash-flow",
        valueLabel: 'Annual CPR',
        format: (v) => `${(v * 100).toFixed(1)}%`,
        avgValue: (v) => v * 100,
        avgSuffix: '%',
        avgDecimals: 1,
        showCurve: true,
      }}
    />
  );
}
