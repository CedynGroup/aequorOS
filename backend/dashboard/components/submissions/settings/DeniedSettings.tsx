import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";

/**
 * Reporting settings are restricted configuration: without the exact
 * regulatory view for this bank the page refuses before it fetches, with a
 * denial that is visibly not an empty or unconfigured state.
 */
export function DeniedSettings({ title, reason }: { title: string; reason: string }) {
  return (
    <SectionCard title={title} subtitle="Restricted configuration">
      <div className="max-w-3xl space-y-2" data-testid="channel-config-denied">
        <StatusPill tone="critical">Access denied</StatusPill>
        <p className="text-body leading-relaxed text-navy">
          You do not have access to these settings.
        </p>
        <p className="text-caption leading-relaxed text-slate">{reason}</p>
      </div>
    </SectionCard>
  );
}
