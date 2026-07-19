'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Menu, X } from 'lucide-react';

const links = [
  { href: '/product', label: 'Product' },
  { href: '/company', label: 'Company' },
  { href: '/contact', label: 'Contact' },
];

// "Client Login" sends users to the sign-in page. In production that is the
// root-level https://aequoros.com/login (NEXT_PUBLIC_LOGIN_URL — served via the
// /login rewrite in next.config.js, which proxies the dashboard's login page);
// in dev it falls back to the local dashboard dev server directly.
const dashboardLoginUrl =
  process.env.NEXT_PUBLIC_LOGIN_URL ??
  `${(process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'http://localhost:3001').replace(/\/$/, '')}/login`;

export default function Navigation() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  return (
    <header
      className={`sticky top-0 z-50 transition-colors duration-200 ${
        scrolled
          ? 'bg-white/85 backdrop-blur-md border-b border-border-light'
          : 'bg-white border-b border-transparent'
      }`}
    >
      <nav className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 h-16 flex items-center justify-between">
        <Link
          href="/"
          className="font-serif font-semibold text-xl text-navy tracking-tight"
          onClick={() => setOpen(false)}
        >
          AequorOS
        </Link>

        <div className="hidden md:flex items-center gap-8">
          <ul className="flex items-center gap-8">
            {links.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  className="relative text-sm font-medium text-text-primary hover:text-navy transition-colors after:content-[''] after:absolute after:left-0 after:-bottom-1 after:h-[2px] after:w-0 after:bg-accent after:transition-all hover:after:w-full"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/contact"
            className="inline-flex items-center justify-center rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-navy-deep transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            Request a demo
          </Link>
          <a
            href={dashboardLoginUrl}
            className="text-sm font-medium text-text-primary hover:text-navy transition-colors"
          >
            Client Login
          </a>
        </div>

        <button
          type="button"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="md:hidden inline-flex items-center justify-center w-10 h-10 rounded-md text-navy hover:bg-soft-bg transition-colors"
        >
          {open ? <X size={22} /> : <Menu size={22} />}
        </button>
      </nav>

      {open && (
        <div className="md:hidden border-t border-border-light bg-white">
          <ul className="px-6 py-6 flex flex-col gap-6">
            {links.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className="block text-lg font-medium text-navy border-l-4 border-accent pl-4"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="px-6 pb-8 space-y-3">
            <Link
              href="/contact"
              onClick={() => setOpen(false)}
              className="inline-flex w-full items-center justify-center rounded-md bg-accent px-5 py-3 text-base font-semibold text-navy-deep transition-colors hover:bg-accent/90"
            >
              Request a demo
            </Link>
            <a
              href={dashboardLoginUrl}
              onClick={() => setOpen(false)}
              className="inline-flex w-full items-center justify-center rounded-md border border-border-light px-5 py-3 text-base font-semibold text-navy transition-colors hover:bg-soft-bg"
            >
              Client Login
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
