import type { Metadata } from 'next';
import Link from 'next/link';
import PageHeader from '@/components/PageHeader';
import ContactForm from '@/components/ContactForm';
import CalendlyInline from '@/components/CalendlyInline';

export const metadata: Metadata = {
  title: 'Contact — AequorOS',
  description:
    'Book a 30-minute walkthrough of AequorOS for Treasury and Risk teams at African banks: the Data Engine, live calculations, and regulatory returns.',
};

export default function ContactPage() {
  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16">
      <PageHeader
        kicker="Contact"
        title="Talk to us about your bank."
        lede="Tell us a little about your institution and we'll come prepared. Every serious inquiry gets a reply."
      />

      <div className="grid lg:grid-cols-[minmax(0,1fr)_380px] gap-10 lg:gap-14 items-start pb-24">
        <ContactForm />

        <div className="flex flex-col gap-5">
          <div className="bg-white border border-hairline rounded-md p-6 flex flex-col gap-2">
            <p className="text-[15px] font-semibold text-ink">
              Prefer to book directly?
            </p>
            <p className="text-sm leading-relaxed text-ink-soft">
              Pick a slot on the calendar and skip the form. Thirty minutes
              with a Treasury or Risk walkthrough.
            </p>
            <CalendlyInline height={520} className="mt-2" />
          </div>

          <div className="bg-white border border-hairline rounded-md p-6 flex flex-col gap-4">
            <div>
              <p className="text-[13px] font-semibold text-text-muted">EMAIL</p>
              <a
                href="mailto:eric@aequoros.com"
                className="mt-1 block text-[14.5px] text-action hover:text-action-dark transition-colors"
              >
                eric@aequoros.com
              </a>
            </div>
            <div>
              <p className="text-[13px] font-semibold text-text-muted">
                WHERE WE ARE
              </p>
              <p className="mt-1 text-[14.5px] text-ink">
                Accra, Ghana · Winchester, Virginia
              </p>
            </div>
            <div>
              <p className="text-[13px] font-semibold text-text-muted">
                BEFORE THE CALL
              </p>
              <p className="mt-1 text-[14.5px] leading-[1.55] text-ink">
                The product interface is public on this site.{' '}
                <Link
                  href="/product"
                  className="font-medium text-action hover:text-action-dark"
                >
                  Browse it first
                </Link>{' '}
                if you like; no login needed.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
