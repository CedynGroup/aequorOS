'use client';

import { PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer } from 'recharts';
import { ChartFrame, Chip } from '@/components/ui';
import { CHART_ACCENT, CHART_CRIT, CHART_GRID, CHART_OK, CHART_WARN } from '@/lib/chartTheme';

/**
 * A semicircular strength gauge for the operating-environment score — the
 * governed [0,1] "how strong is this banking system" figure. Replaces the flat
 * 2px meter with a proper speedometer (recharts RadialBar) whose colour tracks
 * strength bands, framed by the console's ChartFrame so it reads as a
 * first-class visual rather than a progress hint. The sovereign governor cap is
 * disclosed in the frame footer because a published score is held at the cap.
 */

function band(score: number): { color: string; label: string } {
  if (score >= 0.6) return { color: CHART_OK, label: 'Strong' };
  if (score >= 0.4) return { color: CHART_ACCENT, label: 'Moderate' };
  if (score >= 0.2) return { color: CHART_WARN, label: 'Weak' };
  return { color: CHART_CRIT, label: 'Very weak' };
}

export function OeScoreGauge({
  score,
  governorCap,
  governorApplied,
  height = 208,
  title = 'Operating-environment strength',
  subtitle = '0–1 · higher = stronger banking system',
}: {
  score: number;
  governorCap?: number;
  governorApplied?: boolean;
  height?: number;
  title?: string;
  subtitle?: string;
}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(score) ? score : 0));
  const { color, label } = band(clamped);

  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      height={height}
      footer={
        <>
          <span className="inline-flex items-center gap-1.5">
            Strength band <span className="font-medium text-navy">{label}</span>
          </span>
          {governorCap !== undefined && (
            <span className="inline-flex items-center gap-1.5">
              Sovereign cap <span className="font-mono text-navy tnum">{governorCap.toFixed(3)}</span>
            </span>
          )}
          {governorApplied !== undefined &&
            (governorApplied ? (
              <Chip tone="warn">governor binding</Chip>
            ) : (
              <Chip tone="ok">governor slack</Chip>
            ))}
        </>
      }
    >
      <div className="relative h-full">
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart
            innerRadius="68%"
            outerRadius="100%"
            startAngle={180}
            endAngle={0}
            data={[{ name: 'strength', value: clamped }]}
          >
            <PolarAngleAxis type="number" domain={[0, 1]} angleAxisId={0} tick={false} />
            <RadialBar
              dataKey="value"
              angleAxisId={0}
              cornerRadius={12}
              fill={color}
              background={{ fill: CHART_GRID }}
            />
          </RadialBarChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-x-0 bottom-1 flex flex-col items-center">
          <span className="num text-[2.6rem] leading-none text-navy">{clamped.toFixed(3)}</span>
          <span className="mt-1 text-caption text-slate">{label}</span>
        </div>
      </div>
    </ChartFrame>
  );
}
