'use client';

import Script from 'next/script';

export default function CalendlyInline({
  url = 'https://calendly.com/eric-aequoros/30min',
  height = 700,
  className = '',
}: {
  url?: string;
  height?: number;
  className?: string;
}) {
  return (
    <>
      <div
        className={`calendly-inline-widget bg-white rounded-lg overflow-hidden ${className}`}
        data-url={url}
        style={{ minWidth: 320, height }}
      />
      <Script
        src="https://assets.calendly.com/assets/external/widget.js"
        strategy="afterInteractive"
      />
    </>
  );
}
