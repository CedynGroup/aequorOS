import type { Metadata } from 'next';
import { Mail, Linkedin, MapPin, Check } from 'lucide-react';
import SectionLabel from '@/components/SectionLabel';
import ContactForm from '@/components/ContactForm';
import CalendlyInline from '@/components/CalendlyInline';

export const metadata: Metadata = {
  title: 'Request a demo — AequorOS',
  description:
    'Book a 30-minute walkthrough of AequorOS — the data engine, ALM calculations, and regulatory reporting — for Treasury and Risk teams at African banks.',
};

const expectations = [
  'A live walkthrough of the platform: connect → normalize → calculate → report.',
  'Focused on your priorities — liquidity, capital, interest-rate risk, FX, FTP, or regulatory reporting.',
  'Straight answers on core-banking integration, security, and pricing. No obligation.',
];

export default function ContactPage() {
  return (
    <>
      {/* 4.1 Request a demo — hero + form */}
      <section className="relative overflow-hidden bg-navy-deep text-white">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 -right-24 h-96 w-96 rounded-full bg-accent/10 blur-3xl"
        />
        <div className="relative max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-24">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-start">
            {/* Left — value proposition */}
            <div className="lg:pt-4">
              <SectionLabel>REQUEST A DEMO</SectionLabel>
              <h1 className="mt-6 font-serif font-bold text-white text-4xl md:text-5xl lg:text-6xl leading-[1.05] tracking-tight">
                See AequorOS on a bank like yours.
              </h1>
              <p className="mt-6 text-ice-blue text-lg leading-relaxed max-w-[540px]">
                A 30-minute walkthrough of the data engine, ALM calculations, and
                regulatory reporting — built around the workflows and core system
                your bank actually runs.
              </p>

              <ul className="mt-10 space-y-4 max-w-[540px]">
                {expectations.map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15">
                      <Check size={15} className="text-accent" aria-hidden />
                    </span>
                    <span className="text-white/90 leading-relaxed">{item}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-10 pt-8 border-t border-white/10">
                <p className="text-xs font-semibold uppercase tracking-[0.15em] text-ice-blue/70">
                  Prefer to reach out directly?
                </p>
                <div className="mt-4 flex flex-col sm:flex-row sm:items-center gap-4 sm:gap-8">
                  <a
                    href="mailto:eric@aequoros.com"
                    className="inline-flex items-center gap-2 text-white hover:text-accent transition-colors"
                  >
                    <Mail size={18} className="text-accent" aria-hidden />
                    eric@aequoros.com
                  </a>
                  <a
                    href="https://linkedin.com/in/eidanso"
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-2 text-white hover:text-accent transition-colors"
                  >
                    <Linkedin size={18} className="text-accent" aria-hidden />
                    LinkedIn
                  </a>
                </div>
              </div>
            </div>

            {/* Right — request form */}
            <div>
              <h2 className="font-serif font-bold text-white text-2xl">
                Request your walkthrough
              </h2>
              <p className="mt-2 text-ice-blue/80 text-sm">
                Takes under a minute — we&apos;ll follow up to schedule.
              </p>
              <div className="mt-6">
                <ContactForm />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 4.2 Instant scheduler */}
      <section className="bg-soft-bg">
        <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 md:py-20 lg:py-24">
          <div
            id="schedule"
            className="scroll-mt-24 max-w-3xl mb-8"
          >
            <SectionLabel>OR GRAB A TIME NOW</SectionLabel>
            <h2 className="mt-4 font-serif font-bold text-navy text-3xl md:text-4xl leading-tight">
              Pick a slot that works for you.
            </h2>
            <p className="mt-3 text-text-muted text-lg leading-relaxed">
              30 minutes, video call. We&apos;ll learn about your bank and walk
              you through the live platform.
            </p>
          </div>
          <div className="border border-border-light border-l-4 border-l-accent rounded-lg overflow-hidden bg-white shadow-sm">
            <CalendlyInline />
          </div>

          <div className="mt-12 pt-8 border-t border-border-light flex items-center justify-center gap-3 text-text-muted">
            <MapPin size={16} className="text-accent" aria-hidden />
            <span className="text-sm">Winchester, VA · Accra, Ghana</span>
          </div>
        </div>
      </section>
    </>
  );
}
