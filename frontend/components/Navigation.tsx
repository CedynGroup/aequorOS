'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Menu, X } from 'lucide-react';

const links = [
  { href: '/product', label: 'Product' },
  { href: '/security', label: 'Security' },
  { href: '/company', label: 'Company' },
  { href: '/contact', label: 'Contact' },
];

// "Client login" sends users to the dashboard's sign-in page. In production the
// dashboard is a separate app on its own subdomain, so this is the absolute
// https://bank.aequoros.com/login (NEXT_PUBLIC_LOGIN_URL). In dev it falls back
// to the local dashboard dev server (http://localhost:3001/login).
const dashboardLoginUrl =
  process.env.NEXT_PUBLIC_LOGIN_URL ??
  `${(process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'http://localhost:3001').replace(/\/$/, '')}/login`;

// "Employee login" sends AequorOS staff to the internal operator console — a
// separate control-plane app on its own subdomain (docs/internal/staff_UI.md).
const employeeLoginUrl = `${(process.env.NEXT_PUBLIC_CONSOLE_URL ?? 'http://localhost:3002').replace(/\/$/, '')}/login`;

export default function Navigation() {
  const pathname = usePathname();
  // The homepage hero is the site's one dark band; the nav sits on it.
  const dark = pathname === '/';
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

  const shell = dark
    ? scrolled
      ? 'bg-navy-deep/95 backdrop-blur-md border-b border-white/10'
      : 'bg-navy-deep border-b border-white/10'
    : scrolled
      ? 'bg-paper/90 backdrop-blur-md border-b border-hairline'
      : 'bg-paper border-b border-hairline';
  const wordmark = dark ? 'text-white' : 'text-navy-deep';
  const navLink = dark
    ? 'text-white/75 hover:text-white'
    : 'text-ink-soft hover:text-navy-deep';
  const navLinkActive = dark ? 'text-white' : 'text-navy-deep';
  const quiet = dark
    ? 'text-white/55 hover:text-white/85'
    : 'text-text-muted hover:text-navy-deep';
  const cta = dark
    ? 'bg-white text-navy-deep hover:bg-ice-blue'
    : 'bg-navy-deep text-white hover:bg-navy';

  return (
    <header className={`sticky top-0 z-50 transition-colors duration-200 ${shell}`}>
      <nav className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 h-[72px] flex items-center justify-between gap-6">
        <Link
          href="/"
          className="flex items-center gap-2.5"
          onClick={() => setOpen(false)}
        >
          <Image
            src="/images/aequoros-mark.png"
            alt=""
            width={26}
            height={26}
            className="rounded-[5px]"
          />
          <span className={`font-serif font-semibold text-xl tracking-tight ${wordmark}`}>
            AequorOS
          </span>
        </Link>

        <ul className="hidden lg:flex items-center gap-9">
          {links.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className={`text-[14.5px] font-medium transition-colors ${
                  pathname.startsWith(link.href) ? navLinkActive : navLink
                }`}
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>

        <div className="hidden lg:flex items-center gap-5">
          <a href={dashboardLoginUrl} className={`text-[13.5px] transition-colors ${quiet}`}>
            Client login
          </a>
          <a href={employeeLoginUrl} className={`text-[13.5px] transition-colors ${quiet}`}>
            Employee login
          </a>
          <Link
            href="/contact"
            className={`inline-flex h-[42px] items-center rounded px-5 text-[14.5px] font-semibold transition-colors ${cta}`}
          >
            Request a demo
          </Link>
        </div>

        <button
          type="button"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className={`lg:hidden inline-flex items-center justify-center w-10 h-10 rounded-md transition-colors ${
            dark ? 'text-white hover:bg-white/10' : 'text-navy-deep hover:bg-stone'
          }`}
        >
          {open ? <X size={22} /> : <Menu size={22} />}
        </button>
      </nav>

      {open && (
        <div className={`lg:hidden border-t ${dark ? 'border-white/10 bg-navy-deep' : 'border-hairline bg-paper'}`}>
          <ul className="px-6 py-6 flex flex-col gap-5">
            {links.map((link) => (
              <li key={link.href}>
                <Link
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className={`block text-lg font-medium ${dark ? 'text-white' : 'text-navy-deep'}`}
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
              className={`inline-flex w-full items-center justify-center rounded px-5 py-3 text-base font-semibold transition-colors ${
                dark ? 'bg-white text-navy-deep' : 'bg-navy-deep text-white'
              }`}
            >
              Request a demo
            </Link>
            <div className="flex gap-3">
              <a
                href={dashboardLoginUrl}
                onClick={() => setOpen(false)}
                className={`inline-flex flex-1 items-center justify-center rounded border px-5 py-3 text-base font-medium ${
                  dark ? 'border-white/25 text-white' : 'border-hairline text-navy-deep'
                }`}
              >
                Client login
              </a>
              <a
                href={employeeLoginUrl}
                onClick={() => setOpen(false)}
                className={`inline-flex flex-1 items-center justify-center rounded border px-5 py-3 text-base font-medium ${
                  dark ? 'border-white/25 text-white/80' : 'border-hairline text-text-muted'
                }`}
              >
                Employee login
              </a>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
