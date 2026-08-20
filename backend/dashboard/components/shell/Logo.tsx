/** Text-only AequorOS wordmark shared by product surfaces. */
export default function Logo({
  variant = 'dark',
  showWordmark = true,
  className = '',
}: {
  variant?: 'dark' | 'light';
  showWordmark?: boolean;
  className?: string;
}) {
  const wordColor = variant === 'dark' ? '#FFFFFF' : 'rgb(var(--heading))';
  const taglineColor =
    variant === 'dark' ? 'rgba(202, 220, 252, 0.9)' : 'rgb(var(--text-muted))';

  return (
    <div className={`inline-flex items-center ${className}`}>
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
            style={{ color: taglineColor }}
          >
            Treasury Reimagined
          </div>
        </div>
      )}
    </div>
  );
}
