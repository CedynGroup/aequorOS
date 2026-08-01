import Link from 'next/link';
import type { ProductScreen } from '@/lib/product-screens';
import ProductFrame from './ProductFrame';

type Props = {
  screens: ProductScreen[];
  /** Anchor for "see all" deep link on /product */
  seeAllHref?: string;
};

/**
 * Static proof grid for the homepage — no client JS, LCP-friendly with
 * priority on the first frame only.
 */
export default function FeatureScreenGrid({
  screens,
  seeAllHref = '/product#product-ui',
}: Props) {
  return (
    <div>
      <div className="grid gap-8 md:grid-cols-2">
        {screens.map((screen, i) => (
          <ProductFrame
            key={screen.id}
            screen={screen}
            tone="light"
            showCaption
            priority={i === 0}
            sizes="(max-width: 768px) 100vw, 560px"
          />
        ))}
      </div>
      <p className="mt-8 text-center text-sm text-text-muted">
        Synthetic mid-tier universal bank profile · Ghana pilot configuration ·{' '}
        <Link
          href={seeAllHref}
          className="font-semibold text-navy hover:text-accent transition-colors"
        >
          Browse the full product interface
        </Link>
      </p>
    </div>
  );
}
