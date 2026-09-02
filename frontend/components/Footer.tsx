import Link from 'next/link';
import Image from 'next/image';

const dashboardLoginUrl =
  process.env.NEXT_PUBLIC_LOGIN_URL ??
  `${(process.env.NEXT_PUBLIC_DASHBOARD_URL ?? 'http://localhost:3001').replace(/\/$/, '')}/login`;
const employeeLoginUrl = `${(process.env.NEXT_PUBLIC_CONSOLE_URL ?? 'http://localhost:3002').replace(/\/$/, '')}/login`;

const columns = [
  {
    title: 'Product',
    links: [
      { href: '/product', label: 'Platform' },
      { href: '/security', label: 'Security' },
      { href: '/product', label: 'Browse the UI' },
    ],
  },
  {
    title: 'Company',
    links: [
      { href: '/company', label: 'About' },
      { href: '/contact', label: 'Contact' },
      { href: '/investors', label: 'Investors' },
    ],
  },
];

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="bg-navy-deep text-white">
      <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16 pt-16 pb-10">
        <div className="flex flex-col md:flex-row justify-between gap-12">
          <div className="max-w-xs">
            <div className="flex items-center gap-2.5">
              <Image
                src="/images/aequoros-mark.png"
                alt=""
                width={22}
                height={22}
                className="rounded"
              />
              <span className="font-serif font-semibold text-lg">AequorOS</span>
            </div>
            <p className="mt-4 text-[13.5px] leading-relaxed text-white/55">
              Treasury and ALM infrastructure for African banks. Accra ·
              Winchester, VA.
            </p>
          </div>

          <div className="flex flex-wrap gap-x-20 gap-y-10">
            {columns.map((col) => (
              <div key={col.title}>
                <h3 className="text-xs font-semibold tracking-[0.07em] uppercase text-white/45">
                  {col.title}
                </h3>
                <ul className="mt-4 space-y-3">
                  {col.links.map((link) => (
                    <li key={link.href}>
                      <Link
                        href={link.href}
                        className="text-sm text-white/75 hover:text-white transition-colors"
                      >
                        {link.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div>
              <h3 className="text-xs font-semibold tracking-[0.07em] uppercase text-white/45">
                Access
              </h3>
              <ul className="mt-4 space-y-3">
                <li>
                  <a
                    href={dashboardLoginUrl}
                    className="text-sm text-white/75 hover:text-white transition-colors"
                  >
                    Client login
                  </a>
                </li>
                <li>
                  <a
                    href={employeeLoginUrl}
                    className="text-sm text-white/75 hover:text-white transition-colors"
                  >
                    Employee login
                  </a>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <p className="mt-12 pt-6 border-t border-white/10 text-xs text-white/40">
          &copy; {year} AequorOS. Product screens show a synthetic bank profile
          in the Ghana pilot configuration.
        </p>
      </div>
    </footer>
  );
}
