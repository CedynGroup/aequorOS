'use client';

import { useMemo, useState } from 'react';
import { GitCompare } from 'lucide-react';
import type { DeskMethodology } from '@/lib/api';
import { VersionDiff } from '@/components/ui';

/**
 * Side-by-side parameter diff between any two versions of one methodology code
 * — the governance gap the register lacked. A regulator or due-diligence team
 * can now SEE exactly which knobs a Track-2 change moved, rather than eyeballing
 * two parameter trees. Defaults to the two most recent versions (older on the
 * left) and lets either side be re-pointed.
 */
export function MethodologyVersionDiff({ versions }: { versions: DeskMethodology[] }) {
  const ordered = useMemo(() => [...versions].sort((a, b) => a.version - b.version), [versions]);

  const defaultRight = ordered[ordered.length - 1]?.version ?? 0;
  const defaultLeft = ordered[ordered.length - 2]?.version ?? defaultRight;

  const [leftV, setLeftV] = useState<number>(defaultLeft);
  const [rightV, setRightV] = useState<number>(defaultRight);

  if (ordered.length < 2) {
    return (
      <p className="text-caption text-slate">
        Only one version registered — nothing to compare yet. A diff appears once a Track-2 change
        drafts a second version.
      </p>
    );
  }

  const left = ordered.find((v) => v.version === leftV) ?? ordered[ordered.length - 2];
  const right = ordered.find((v) => v.version === rightV) ?? ordered[ordered.length - 1];

  const selectClass =
    'rounded-md border border-border bg-surface-base px-2.5 py-1 font-mono text-caption text-ink focus:border-focus focus:outline-none';

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
        <GitCompare size={14} className="text-slate" aria-hidden />
        <span>Compare</span>
        <select
          aria-label="Left version"
          className={selectClass}
          value={leftV}
          onChange={(e) => setLeftV(Number(e.target.value))}
        >
          {ordered.map((v) => (
            <option key={v.id} value={v.version}>
              v{v.version} · {v.status}
            </option>
          ))}
        </select>
        <span aria-hidden>→</span>
        <select
          aria-label="Right version"
          className={selectClass}
          value={rightV}
          onChange={(e) => setRightV(Number(e.target.value))}
        >
          {ordered.map((v) => (
            <option key={v.id} value={v.version}>
              v{v.version} · {v.status}
            </option>
          ))}
        </select>
      </div>
      <VersionDiff
        left={{ label: `v${left.version}`, value: left.parameters }}
        right={{ label: `v${right.version}`, value: right.parameters }}
      />
    </div>
  );
}
