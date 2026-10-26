import RegulatoryModuleTabs from "@/components/shell/RegulatoryModuleTabs";

// Gap-ladder note: the LCR engine materializes category-level line items
// (runoff-weighted outflows / inflows over a single 30-day stressed horizon),
// not maturity-bucketed flows, so there is no dedicated Gap Ladder tab — the
// outflow/inflow decomposition lives on the Cockpit instead.
const tabs = [
  { href: "/liquidity", ruleCode: "LCR-NSFR", label: "Cockpit" },
  { href: "/liquidity/buffer", ruleCode: "LCR-NSFR", label: "Buffer" },
  { href: "/liquidity/nsfr", ruleCode: "LCR-NSFR", label: "NSFR" },
  { href: "/liquidity/forecast", label: "Cash Flow Forecast" },
  { href: "/liquidity/stress", ruleCode: "LCR-NSFR", label: "Scenarios" },
  { href: "/liquidity/monitoring", ruleCode: "LMT", label: "Monitoring Tools" },
  { href: "/liquidity/cfp", label: "CFP" },
];

export default function LiquidityLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <RegulatoryModuleTabs tabs={tabs}>{children}</RegulatoryModuleTabs>
    </>
  );
}
