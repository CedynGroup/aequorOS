'use client';

import BehavioralModelPage from '@/components/behavioral/BehavioralModelPage';

export default function DepositStabilityPage() {
  return (
    <BehavioralModelPage
      slug="deposit-stability"
      config={{
        title: 'Deposit Stability',
        subtitle:
          "Stable (sticky) fraction of each deposit product under stress, learned from balance retention · feeds LCR",
        valueLabel: 'Stable fraction',
        format: (v) => `${(v * 100).toFixed(0)}%`,
        avgValue: (v) => v * 100,
        avgSuffix: '%',
        avgDecimals: 0,
      }}
    />
  );
}
