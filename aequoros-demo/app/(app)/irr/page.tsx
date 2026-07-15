import ModulePlaceholder from '@/components/ui/ModulePlaceholder';

export default function IRRModule() {
  return (
    <ModulePlaceholder
      moduleNumber="01"
      title="Interest Rate Risk"
      subtitle="Banking book IRRBB · BoG CRD tenor framework · Gap, NII, EVE"
      screens={[
        'IRR Dashboard — repricing gap by tenor bucket, NII and EVE sensitivity',
        'Rate Scenarios — standard BoG / Basel IRRBB shocks plus custom scenario builder',
        'Position Viewer — IRS portfolio with hedge accounting under IFRS 9',
        'Hedging Recommendations — hedge coverage analysis for EVE buffer protection',
      ]}
      capabilities={[
        'Repricing gap analysis across the BoG CRD tenor framework',
        'Net Interest Income sensitivity to parallel and non-parallel rate shocks',
        'Economic Value of Equity impact under the six standardized IRRBB scenarios',
        'Interest rate swap book management with hedge effectiveness tracking',
      ]}
    />
  );
}
