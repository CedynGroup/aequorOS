import ModuleTabs from "@/components/shell/ModuleTabs";

// One shell for both ways into Settings: the sidebar's "Settings" opens General
// and the avatar menu's "Profile & preferences" opens the personal tab. Every
// session can open both, and each setting lives on exactly one of them.
const tabs = [
  { href: "/settings", label: "General" },
  { href: "/settings/profile", label: "Profile & preferences" },
];

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ModuleTabs tabs={tabs} />
      {children}
    </>
  );
}
