import Image from 'next/image';

export default function BrandLogo({
  inverse = false,
  subtitle,
  className = '',
}: {
  inverse?: boolean;
  subtitle?: string;
  className?: string;
}) {
  return (
    <div className={`inline-flex items-center gap-2.5 ${className}`}>
      <Image
        src="/branding/aequoros-mark.png"
        alt="AequorOS"
        width={32}
        height={32}
        priority
        className="h-8 w-8 shrink-0"
      />
      <div className="min-w-0 leading-none">
        <div className={`font-serif text-xl font-semibold ${inverse ? 'text-white' : 'text-navy'}`}>
          AequorOS
        </div>
        {subtitle && (
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