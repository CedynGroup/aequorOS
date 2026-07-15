import ModulePlaceholder from '@/components/ui/ModulePlaceholder';

export default function FXModule() {
  return (
    <ModulePlaceholder
      moduleNumber="03"
      title="FX Risk"
      subtitle="Net open position by currency · BoG NOP framework"
      screens={[
        'FX Exposure Dashboard — net open position by currency in GHS equivalent',
        'Currency Scenarios — cedi depreciation P&L impact calibrated to BoG ICAAP',
        'Rate Prediction — 30/60/90-day forecasts compared to forward market implieds',
        'Hedging Dashboard — active hedges, expiring positions, and restructure decisions',
      ]}
      capabilities={[
        'Net open position monitoring against the BoG NOP limit framework',
        'Position breakdown across assets, liabilities, and derivatives by currency',
        'Currency stress scenarios with hedge-adjusted P&L impact',
        'Forward and swap hedge book management with expiry tracking',
      ]}
    />
  );
}
