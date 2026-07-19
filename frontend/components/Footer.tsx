import Link from 'next/link';
import { Linkedin, Mail, MapPin } from 'lucide-react';

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="bg-navy-deep text-white">
      <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 py-16 grid gap-12 md:grid-cols-3">
        <div>
          <div className="font-serif font-semibold text-2xl">AequorOS</div>
          <p className="mt-3 text-ice-blue text-sm max-w-xs">
            Treasury and ALM infrastructure for African banks.
          </p>
          <p className="mt-6 text-xs text-ice-blue/70">
            &copy; {year} AequorOS. All rights reserved.
          </p>
        </div>

        <div>
          <h3 className="text-xs font-semibold tracking-[0.15em] uppercase text-accent">
            Site
          </h3>
          <ul className="mt-4 space-y-3 text-sm">
            <li>
              <Link href="/" className="hover:text-accent transition-colors">
                Home
              </Link>
            </li>
            <li>
              <Link
                href="/product"
                className="hover:text-accent transition-colors"
              >
                Product
              </Link>
            </li>
            <li>
              <Link
                href="/company"
                className="hover:text-accent transition-colors"
              >
                Company
              </Link>
            </li>
            <li>
              <Link
                href="/contact"
                className="hover:text-accent transition-colors"
              >
                Contact
              </Link>
            </li>
            <li>
              <Link
                href="/investors"
                className="hover:text-accent transition-colors"
              >
                Investors
              </Link>
            </li>
            <li>
              <a
                href="https://demo.aequoros.com"
                target="_blank"
                rel="noreferrer"
                className="hover:text-accent transition-colors"
              >
                Platform demo
              </a>
            </li>
          </ul>
        </div>

        <div>
          <h3 className="text-xs font-semibold tracking-[0.15em] uppercase text-accent">
            Contact
          </h3>
          <ul className="mt-4 space-y-3 text-sm">
            <li>
              <a
                href="mailto:eric@aequoros.com"
                className="inline-flex items-center gap-2 hover:text-accent transition-colors"
              >
                <Mail size={16} className="text-accent" />
                eric@aequoros.com
              </a>
            </li>
            <li>
              <a
                href="https://linkedin.com/in/eidanso"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 hover:text-accent transition-colors"
              >
                <Linkedin size={16} className="text-accent" />
                linkedin.com/in/eidanso
              </a>
            </li>
            <li className="inline-flex items-center gap-2 text-ice-blue/80">
              <MapPin size={16} className="text-accent" />
              Winchester, VA · Accra, Ghana
            </li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
