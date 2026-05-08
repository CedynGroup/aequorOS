/**
 * Equilibrium Mark — triangle resting on a horizontal line.
 * Per design brief: "represents balance sheet equilibrium".
 */
export default function Logo({
  variant = 'dark',
  showWordmark = true,
  className = '',
}: {
  variant?: 'dark' | 'light';
  showWordmark?: boolean;
  className?: string;
}) {
  const lineColor = variant === 'dark' ? '#FFFFFF' : '#0A2540';
  const triangleColor = variant === 'dark' ? '#2D7FF9' : '#2D7FF9';
  const wordColor = variant === 'dark' ? '#FFFFFF' : '#0A2540';

  return (
    <div className={`inline-flex items-center gap-2.5 ${className}`}>
      <svg
        width="28"
        height="28"
        viewBox="0 0 64 64"
        role="img"
        aria-label="AequorOS Equilibrium Mark"
      >
        <line
          x1="14"
          y1="46"
          x2="50"
          y2="46"
          stroke={lineColor}
          strokeWidth="3"
          strokeLinecap="round"
        />
        <polygon
          points="32,18 46,42 18,42"
          fill="none"
          stroke={triangleColor}
          strokeWidth="3"
          strokeLinejoin="round"
        />
      </svg>
      {showWordmark && (
        <div className="leading-none">
          <div
            className="text-h2 font-semibold tracking-tight"
            style={{ color: wordColor }}
          >
            AequorOS
          </div>
          <div
            className="text-[9px] font-medium uppercase tracking-[0.2em] mt-0.5"
            style={{ color: variant === 'dark' ? '#CADCFC' : '#5A6776' }}
          >
            Treasury Reimagined
          </div>
        </div>
      )}
    </div>
  );
}
