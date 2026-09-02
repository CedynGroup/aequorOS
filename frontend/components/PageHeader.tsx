import type { ReactNode } from 'react';
import Kicker from './Kicker';

/** Inner-page header: kicker, display headline, lede — light ground. */
export default function PageHeader({
  kicker,
  title,
  lede,
  maxWidth = 'max-w-3xl',
}: {
  kicker: string;
  title: string;
  lede?: ReactNode;
  maxWidth?: string;
}) {
  return (
    <div className={`pt-16 md:pt-20 pb-10 md:pb-14 flex flex-col gap-5 ${maxWidth}`}>
      <Kicker>{kicker}</Kicker>
      <h1 className="font-serif font-medium text-4xl md:text-5xl lg:text-[56px] leading-[1.08] tracking-tight text-ink">
        {title}
      </h1>
      {lede ? (
        <p className="text-lg leading-relaxed text-ink-soft">{lede}</p>
      ) : null}
    </div>
  );
}
