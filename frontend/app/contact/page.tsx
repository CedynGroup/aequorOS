import type { Metadata } from 'next';
import { Mail, Calendar, Linkedin, MapPin } from 'lucide-react';
import SectionLabel from '@/components/SectionLabel';
import ContactForm from '@/components/ContactForm';
import CalendlyInline from '@/components/CalendlyInline';

export const metadata: Metadata = {
  title: 'Contact — AequorOS',
  description:
    'Get in touch with AequorOS — for bank executives, investors, advisors, and engineers interested in Treasury and ALM infrastructure for African banks.',
};

const options = [
  {
    Icon: Mail,
    title: 'Direct email',
    content:
      'For introductions, investor conversations, and partnership discussions.',
    linkLabel: 'eric@aequoros.com',
    href: 'mailto:eric@aequoros.com',
    external: false,
  },
  {
    Icon: Calendar,
    title: 'Book a demo',
    content:
      '30 minutes to understand your situation and walk you through the live platform.',
    linkLabel: 'See available times \u2193',
    href: '#schedule',
    external: false,
  },
  {
    Icon: Linkedin,
    title: 'Connect on LinkedIn',
    content: 'Follow along as we build, or reach out directly.',
    linkLabel: 'linkedin.com/in/eidanso',
    href: 'https://linkedin.com/in/eidanso',
    external: true,
  },
];

export default function ContactPage() {
  return (
    <>
      {/* 4.1 Contact Hero */}
      <section className="bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-24 lg:py-28">
          <div className="max-w-3xl">
            <h1 className="font-serif font-bold text-navy text-4xl md:text-5xl lg:text-6xl leading-[1.1]">
              Let&apos;s talk.
            </h1>
            <p className="mt-8 text-text-muted text-lg leading-relaxed max-w-[600px]">
              Whether you&apos;re a Treasury or Risk leader considering a pilot,
              an investor evaluating the opportunity, a potential advisor, or an
              engineer interested in joining the team — we&apos;d like to hear
              from you.
            </p>
          </div>
        </div>
      </section>

      {/* 4.2 Contact Options */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pb-16 md:pb-20 lg:pb-24 pt-16 md:pt-20">
          <div className="grid gap-10 lg:grid-cols-2 items-start">
            <div className="space-y-6">
              <SectionLabel>WAYS TO REACH US</SectionLabel>
              {options.map(
                ({ Icon, title, content, linkLabel, href, external }) => (
                  <article
                    key={title}
                    className="bg-white border border-border-light border-l-4 border-l-accent rounded-lg p-7"
                  >
                    <div className="flex items-start gap-4">
                      <div className="w-10 h-10 rounded-md bg-navy-deep flex items-center justify-center shrink-0">
                        <Icon size={18} className="text-accent" aria-hidden />
                      </div>
                      <div>
                        <h3 className="font-serif font-bold text-navy text-xl">
                          {title}
                        </h3>
                        <p className="mt-2 text-text-primary leading-relaxed">
                          {content}
                        </p>
                        <a
                          href={href}
                          target={external ? '_blank' : undefined}
                          rel={external ? 'noreferrer' : undefined}
                          className="mt-3 inline-block text-accent hover:text-navy font-medium transition-colors"
                        >
                          {linkLabel}
                        </a>
                      </div>
                    </div>
                  </article>
                )
              )}
            </div>

            <ContactForm />
          </div>

          {/* 4.3 Inline scheduler */}
          <div id="schedule" className="mt-20 pt-12 border-t border-border-light scroll-mt-24">
            <div className="max-w-3xl mb-8">
              <SectionLabel>BOOK A MEETING</SectionLabel>
              <h2 className="mt-4 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
                Pick a time that works for you.
              </h2>
              <p className="mt-3 text-text-muted text-lg leading-relaxed">
                30 minutes, video call. We&apos;ll learn about your situation and
                walk you through the live platform.
              </p>
            </div>
            <div className="border border-border-light border-l-4 border-l-accent rounded-lg overflow-hidden bg-white">
              <CalendlyInline />
            </div>
          </div>

          {/* 4.4 Location Info */}
          <div className="mt-16 pt-8 border-t border-border-light flex items-center justify-center gap-3 text-text-muted">
            <MapPin size={16} className="text-accent" aria-hidden />
            <span className="text-sm">Winchester, VA · Accra, Ghana</span>
          </div>
        </div>
      </section>
    </>
  );
}
