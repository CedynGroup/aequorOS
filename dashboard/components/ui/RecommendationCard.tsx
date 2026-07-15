import { Sparkles, Check, X, Edit3 } from 'lucide-react';
import StatusPill, { type StatusTone } from './StatusPill';

export default function RecommendationCard({
  modelLabel,
  title,
  rationale,
  expectedImpact,
  confidence,
  severity = 'action',
}: {
  modelLabel: string;
  title: string;
  rationale: string;
  expectedImpact: string;
  confidence: number; // 0..1
  severity?: StatusTone;
}) {
  return (
    <div className="card p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-8 h-8 rounded bg-action-light text-action">
            <Sparkles size={16} aria-hidden />
          </span>
          <div>
            <p className="text-caption font-medium text-slate uppercase tracking-wider">
              {modelLabel}
            </p>
            <p className="text-caption text-slate">
              Confidence{' '}
              <span className="font-mono font-medium text-navy tabular-nums">
                {(confidence * 100).toFixed(0)}%
              </span>
            </p>
          </div>
        </div>
        <StatusPill tone={severity}>{severity === 'action' ? 'AI' : severity}</StatusPill>
      </div>

      <h4 className="text-h3 text-navy">{title}</h4>

      <div className="space-y-3 text-body text-navy/85 leading-relaxed">
        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Rationale
          </p>
          <p className="mt-1">{rationale}</p>
        </div>
        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Expected impact
          </p>
          <p className="mt-1">{expectedImpact}</p>
        </div>
      </div>

      <div className="border-t border-border-light pt-3 flex items-center gap-2">
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-caption font-medium text-action hover:text-action-hover px-3 py-1.5 rounded border border-action/30 bg-action-light"
        >
          <Check size={13} aria-hidden /> Accept
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-caption font-medium text-slate hover:text-navy px-3 py-1.5 rounded border border-border"
        >
          <Edit3 size={13} aria-hidden /> Modify
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-caption font-medium text-slate hover:text-critical px-3 py-1.5 rounded border border-border"
        >
          <X size={13} aria-hidden /> Reject
        </button>
      </div>
    </div>
  );
}
