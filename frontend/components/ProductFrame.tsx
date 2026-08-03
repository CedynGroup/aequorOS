import Image from 'next/image';
import type { ProductScreen } from '@/lib/product-screens';

type Tone = 'dark' | 'light';

type Props = {
  screen: ProductScreen;
  priority?: boolean;
  tone?: Tone;
  /** Show title + caption under the frame */
  showCaption?: boolean;
  className?: string;
  sizes?: string;
};

/**
 * Browser-chrome product capture — the primary proof surface for bank and
 * compliance reviewers who need to see a launched interface without logging in.
 */
export default function ProductFrame({
  screen,
  priority = false,
  tone = 'dark',
  showCaption = false,
  className = '',
  sizes = '(max-width: 768px) 100vw, (max-width: 1280px) 90vw, 1100px',
}: Props) {
  const chrome =
    tone === 'dark'
      ? 'bg-navy-deep border-white/10 shadow-2xl shadow-black/40'
      : 'bg-white border-border-light shadow-xl shadow-navy/10';
  const bar =
    tone === 'dark'
      ? 'bg-black/35 border-white/10'
      : 'bg-soft-bg border-border-light';
  const dot = tone === 'dark' ? 'bg-white/25' : 'bg-text-muted/30';
  const urlText = tone === 'dark' ? 'text-ice-blue/70' : 'text-text-muted';

  return (
    <figure className={className}>
      <div className={`rounded-xl overflow-hidden border ${chrome}`}>
        <div
          className={`flex items-center gap-2 px-3 sm:px-4 py-2.5 border-b ${bar}`}
          aria-hidden
        >
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
          <span
            className={`ml-2 sm:ml-3 flex-1 truncate rounded-md px-2.5 py-1 text-[11px] sm:text-xs font-medium ${urlText} ${
              tone === 'dark' ? 'bg-white/5' : 'bg-white border border-border-light'
            }`}
          >
            bank.aequoros.com · {screen.label}
          </span>
        </div>
        <Image
          src={screen.src}
          alt={screen.alt}
          width={2880}
          height={1800}
          priority={priority}
          sizes={sizes}
          className="w-full h-auto block"
        />
      </div>
      {showCaption && (
        <figcaption className="mt-4 max-w-3xl">
          <p className="font-serif font-bold text-navy text-lg sm:text-xl">
            {screen.title}
          </p>
          <p className="mt-2 text-text-muted text-sm sm:text-base leading-relaxed">
            {screen.caption}
          </p>
        </figcaption>
      )}
    </figure>
  );
}
