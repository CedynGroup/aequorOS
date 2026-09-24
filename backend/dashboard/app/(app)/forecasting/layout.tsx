import ModuleTabs from "@/components/shell/ModuleTabs";

import { forecastingTabs } from "@/components/forecasting/tabs";

export default function ForecastingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ModuleTabs tabs={forecastingTabs} />
      {children}
    </>
  );
}
