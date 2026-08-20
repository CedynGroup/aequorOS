export default function BrandLogo({
  inverse = false,
  subtitle,
  markOnly = false,
  className = '',
}: {
  inverse?: boolean;
  subtitle?: string;
  /** Compact text treatment for constrained shell chrome. */
  markOnly?: boolean;
  className?: string;
}) {
  return (
    <div className={`inline-flex items-center ${className}`}>
      <div className="min-w-0 leading-none">
        <div
          className={`font-serif font-semibold ${
            markOnly ? 'text-sm' : 'text-xl'
          } ${inverse ? 'text-white' : 'text-navy'}`}
        >
          AequorOS
        </div>
        {!markOnly && subtitle && (
          <div
            className={`mt-1 text-micro uppercase tracking-widest ${
              inverse ? 'text-slate' : 'text-slate'
            }`}
          >
            {subtitle}
          </div>
        )}
      </div>
    </div>
  );
}