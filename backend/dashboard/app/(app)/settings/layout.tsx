import ModuleTabs from '@/components/shell/ModuleTabs';

// One shell for both ways into Settings. The sidebar's "Settings" opens the
// organization hub and the avatar menu's "Profile & preferences" opens the
// personal page; without a shared strip they read as two unrelated screens.
// The strip follows the same access rules as every module tab: the
// Organization tab exists only for organization-wide Account administration,
// the personal tab for every session.
const tabs = [
  { href: '/settings', label: 'Organization' },
  { href: '/settings/profile', label: 'Profile & preferences' },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
