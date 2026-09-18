'use client';

import BehavioralModelPage from '@/components/behavioral/BehavioralModelPage';

export default function NmdDurationPage() {
  return (
    <BehavioralModelPage
      slug="nmd-duration"
      config={{
        title: 'NMD Duration',
        valueLabel: 'Effective duration',
        format: (v) => `${v.toFixed(0)} mo`,
        avgValue: (v) => v,
        avgSuffix: 'mo',
        avgDecimals: 0,
        showCore: true,
        feeds: [
          { label: 'Feeds IRRBB assumptions', href: '/irr' },
          { label: 'Feeds FTP', href: '/ftp' },
        ],
      }}
    />
  );
}
