import {
  Activity,
  Building2,
  DollarSign,
  FileSearch,
  Gauge,
  History,
  LayoutDashboard,
  LineChart,
  ScrollText,
  SearchCheck,
  Send,
  Shield,
  Table2,
  UserPlus,
  Users,
  Waypoints,
  type LucideIcon,
} from 'lucide-react';

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Extra fuzzy-search terms not present in the label. */
  keywords?: string;
};

export type NavGroup = {
  label: string;
  items: NavItem[];
};

/**
 * The single source of truth for the console's rail nav — consumed by both
 * Sidebar and CommandPalette so they never drift. Grouped per the operator
 * disciplines: Overview, Tenants, Markets Desk, Admin.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      {
        href: '/',
        label: 'Home',
        icon: LayoutDashboard,
        keywords: 'cockpit overview dashboard start',
      },
    ],
  },
  {
    label: 'Tenants',
    items: [
      { href: '/tenants', label: 'Tenants', icon: Building2, keywords: 'banks orgs institutions BK OR' },
      { href: '/onboard', label: 'Onboard', icon: UserPlus, keywords: 'provision new tenant saga create bank' },
      { href: '/operations', label: 'Operations', icon: Activity, keywords: 'data engines connections jobs' },
    ],
  },
  {
    label: 'Markets Desk',
    items: [
      { href: '/desk/market', label: 'Market', icon: LineChart, keywords: 'workspace rates curves fx overview' },
      { href: '/desk/determinations', label: 'Determinations', icon: ScrollText, keywords: 'research desk weekly cob draft approve' },
      { href: '/desk/curves', label: 'Curves', icon: Waypoints, keywords: 'forward zero discount definitions construction' },
      { href: '/desk/fx-forwards', label: 'FX Forwards', icon: DollarSign, keywords: 'cip outright points pairs' },
      { href: '/desk/operating-environment', label: 'Operating Environment', icon: Gauge, keywords: 'bicra score jurisdiction risk' },
      { href: '/desk/methodology', label: 'Methodology', icon: ScrollText, keywords: 'register track 2 versions parameters' },
      { href: '/desk/observations', label: 'Observations', icon: Table2, keywords: 'series manual entry captures values' },
      { href: '/desk/sources', label: 'Sources', icon: FileSearch, keywords: 'captures silver parser bog gfim' },
      { href: '/desk/entitlements', label: 'Entitlements', icon: Shield, keywords: 'datasets tiers grants org access' },
      { href: '/desk/publications', label: 'Publications', icon: Send, keywords: 'fan-out publish golden copy banks' },
    ],
  },
  {
    label: 'Admin',
    items: [
      { href: '/admin/operators', label: 'Operators', icon: Users, keywords: 'staff users roles password reset' },
      { href: '/admin/audit', label: 'Audit Log', icon: History, keywords: 'operator_audit_log actions history' },
      { href: '/admin/inspector', label: 'Tenant Inspector', icon: SearchCheck, keywords: 'impersonate read-only cross-tenant session' },
    ],
  },
];

export const ALL_NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

/** Whether a nav href is active for the current pathname (home is exact). */
export function isNavActive(href: string, pathname: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}
