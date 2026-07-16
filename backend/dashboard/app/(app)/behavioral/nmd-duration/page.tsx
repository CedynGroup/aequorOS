'use client';

import BehavioralModelPage from '@/components/behavioral/BehavioralModelPage';

export default function NmdDurationPage() {
  return (
    <BehavioralModelPage
      slug="nmd-duration"
      config={{
        title: 'NMD Duration',
        subtitle:
          "Effective behavioral duration of non-maturity deposits, learned from the bank's balance history · feeds IRR & FTP",
        valueLabel: 'Effective duration',
        format: (v) => `${v.toFixed(0)} mo`,
        avgValue: (v) => v,
        avgSuffix: 'mo',
        avgDecimals: 0,
        showCore: true,
      }}
    />
  );
}
