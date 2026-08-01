import Image from 'next/image';
import type { ProductScreen } from '@/lib/product-screens';

export default function ModuleCard({
  number,
  name,
  description,
  detail,
  screen,
}: {
  number: string;
  name: string;
  description: string;
  /** Live capability note — not roadmap padding */
  detail?: string;
  screen?: ProductScreen;
}) {
  return (
    <article className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg overflow-hidden h-full flex flex-col">
      {screen && (
        <div className="relative aspect-[16/10] border-b border-border-light bg-soft-bg">
          <Image
            src={screen.src}
            alt={screen.alt}
            fill
            sizes="(max-width: 768px) 100vw, 50vw"
            className="object-cover object-top"
          />
        </div>
      )}
      <div className="px-8 py-8 flex flex-col flex-1">
        <p className="font-serif text-accent text-lg">Module {number}</p>
        <h3 className="mt-2 font-serif font-bold text-navy text-2xl leading-snug">
          {name}
        </h3>
        <p className="mt-4 text-text-primary text-base md:text-lg leading-relaxed">
          {description}
        </p>
        {detail && (
          <p className="mt-5 text-text-muted text-sm leading-relaxed border-t border-border-light pt-5">
            {detail}
          </p>
        )}
      </div>
    </article>
  );
}
