import Image from 'next/image';
import type { ProductScreen } from '@/lib/product-screens';

type Variant = 'chrome-dark' | 'plain';

type Props = {
  screen: ProductScreen;
  priority?: boolean;
  /**
   * 'chrome-dark' — browser-chrome treatment for the homepage hero on the navy
   * band. 'plain' — hairline card with a soft shadow, the redesign's standard
   * treatment everywhere else.
   */
  variant?: Variant;
  className?: string;
  /** Must describe the layout slot exactly — one width fetched per view. */
  sizes: string;
};

/**
 * Product capture — the site's proof surface. Captions live at call sites so
 * each one appears exactly once across the site.
 */
export default function ProductFrame({
  screen,
  priority = false,
  variant = 'plain',
  className = '',
  sizes,
}: Props) {
  if (variant === 'chrome-dark') {
    return (
      <figure className={className}>
        <div className="rounded-t-lg overflow-hidden border border-white/[0.18] border-b-0 shadow-[0_-18px_60px_rgba(0,0,0,0.35)]">
          <div
            className="flex items-center gap-2 h-[34px] px-3.5 bg-[#17202F] border-b border-white/[0.08]"
            aria-hidden
          >
            <span className="h-[9px] w-[9px] rounded-full bg-white/[0.18]" />
            <span className="h-[9px] w-[9px] rounded-full bg-white/[0.18]" />
            <span className="ml-2 truncate text-[11.5px] text-white/45">
              bank.aequoros.com · {screen.label}
            </span>
          </div>
          <Image
            src={screen.src}
            alt={screen.alt}
            width={2880}
            height={1800}
            priority={priority}
            placeholder="blur"
            blurDataURL={screen.blurDataURL}
            sizes={sizes}
            className="block w-full h-auto"
          />
        </div>
      </figure>
    );
  }

  return (
    <figure className={className}>
      <div className="rounded-lg overflow-hidden border border-[#DDDACF] shadow-[0_12px_40px_rgba(15,24,69,0.10)]">
        <Image
          src={screen.src}
          alt={screen.alt}
          width={2880}
          height={1800}
          priority={priority}
          placeholder="blur"
          blurDataURL={screen.blurDataURL}
          sizes={sizes}
          className="block w-full h-auto"
        />
      </div>
    </figure>
  );
}
