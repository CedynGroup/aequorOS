import ModulePlaceholder from '@/components/ui/ModulePlaceholder';

export default function FTPModule() {
  return (
    <ModulePlaceholder
      moduleNumber="05"
      title="Funds Transfer Pricing"
      subtitle="Daily-bootstrapped funding curve · BoG T-bill auction + interbank market"
      screens={[
        'FTP Yield Curve — funding curve bootstrapped from BoG T-bill auctions and interbank rates',
        'Branch Profitability — FTP-adjusted NIM by branch across regions',
        'Product Profitability — FTP-adjusted spread by product line on a match-funded basis',
        'FTP Rates Table — active rates by product class, refreshed from BoG auction outcomes',
      ]}
      capabilities={[
        'Yield curve construction from BoG and market rates with spread adjustment',
        'Match-funded transfer pricing applied to all balance-sheet positions',
        'Branch and product P&L attribution net of funding costs',
        'Rate history tracking against the BoG auction trajectory',
      ]}
    />
  );
}
