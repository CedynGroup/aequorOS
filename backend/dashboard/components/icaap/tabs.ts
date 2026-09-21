/**
 * The ICAAP cycle's workspace tabs.
 *
 * Later-phase tabs are DECLARED here and disabled — so the shape of the
 * finished workspace is recorded in one place — but they render nothing: the
 * layout passes only enabled tabs to `ModuleTabs`, and there is no route file
 * behind a disabled one, so a typed URL is a genuine 404 rather than a
 * placeholder screen promising a feature that does not exist.
 */

export type IcaapTab = {
  segment: string;
  label: string;
  phase: "P1" | "P2" | "P3";
  enabled: boolean;
};

export const ICAAP_CYCLE_TABS: readonly IcaapTab[] = [
  { segment: "overview", label: "Overview", phase: "P1", enabled: true },
  { segment: "sections", label: "Sections", phase: "P1", enabled: true },
  { segment: "risks", label: "Risk register", phase: "P2", enabled: true },
  { segment: "appetite", label: "Risk appetite", phase: "P2", enabled: true },
  { segment: "pillar2", label: "Pillar 2", phase: "P2", enabled: true },
  {
    segment: "stress",
    label: "Stress & capital plan",
    phase: "P2",
    enabled: true,
  },
  {
    // The stage timeline, plus `AuditReview` and `ChallengeLog` — built with
    // P2 and mounted here unchanged.
    segment: "review",
    label: "Review & challenge",
    phase: "P3",
    enabled: true,
  },
  { segment: "attachments", label: "Attachments", phase: "P1", enabled: true },
  { segment: "filing", label: "Filing", phase: "P3", enabled: true },
];

export function icaapTabHrefs(cycleId: string): { href: string; label: string }[] {
  return ICAAP_CYCLE_TABS.filter((tab) => tab.enabled).map((tab) => ({
    href: `/icaap/${cycleId}/${tab.segment}`,
    label: tab.label,
  }));
}
