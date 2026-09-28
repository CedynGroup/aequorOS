import ModuleTabs from "@/components/shell/ModuleTabs";

/**
 * Explore, the figures this institution defined for itself, and the two things a
 * reader does with a question they have built.
 *
 * WHY HERE. A threshold alert is one figure and a line; a scheduled report is a
 * question, a schedule and a distribution list. Both are a question plus an
 * instruction, and Explore is where a question is built — the same `BiQuery` the
 * grid submits is what a report stores. It is also where a recipient who could
 * not be sent a confidential report is already directed to sign in
 * (`services/bi/subscriptions.SIGN_IN_PATH`), so the surface they land on is the
 * one that owns the report they were told about.
 *
 * Deliberately not `/alerts`: the Alert Center is the live modules' own
 * limit-breach findings, computed by the platform against regulatory and board
 * limits. A threshold somebody set for themselves on a figure they chose is a
 * different object with a different owner, and putting the two on one page would
 * make a personal watch look like a supervisory one.
 *
 * Calculated measures sit here for the same reason. A formula is a figure this
 * institution defined, and the only thing anyone does with a figure is ask a
 * question with it — so the place to write one is beside the place the questions
 * are built, and the certified ones appear in Explore's own measure list.
 */
const tabs = [
  { href: "/explore", label: "Explore" },
  { href: "/explore/measures", label: "Calculated measures" },
  { href: "/explore/alerts", label: "Threshold alerts" },
  { href: "/explore/subscriptions", label: "Scheduled reports" },
];

export default function ExploreLayout({
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
