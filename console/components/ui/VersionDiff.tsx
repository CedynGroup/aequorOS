'use client';

import { useMemo, useState, type ReactNode } from 'react';

type DiffStatus = 'added' | 'removed' | 'changed' | 'unchanged';

type DiffRow = {
  path: string;
  left: string | null;
  right: string | null;
  status: DiffStatus;
};

/** Flatten a JSON-ish value into dot-path → stringified-leaf pairs. */
function flatten(value: unknown, prefix = '', out: Map<string, string> = new Map()): Map<string, string> {
  if (value === null || value === undefined) {
    if (prefix) out.set(prefix, value === null ? 'null' : 'undefined');
    return out;
  }
  if (typeof value !== 'object') {
    out.set(prefix || '(value)', String(value));
    return out;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) out.set(prefix, '[]');
    value.forEach((v, i) => flatten(v, prefix ? `${prefix}[${i}]` : `[${i}]`, out));
    return out;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) out.set(prefix, '{}');
  for (const [k, v] of entries) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  return out;
}

const STATUS_STYLE: Record<DiffStatus, string> = {
  added: 'bg-success-light/40',
  removed: 'bg-critical-light/40',
  changed: 'bg-warning-light/40',
  unchanged: '',
};

/**
 * Side-by-side parameter / JSON diff for governed versions (methodology
 * register, curve definitions). Flattens both sides to dot-paths and highlights
 * added / removed / changed leaves — the governance gap the console lacked.
 */
export function VersionDiff({
  left,
  right,
  className = '',
  defaultShowUnchanged = false,
}: {
  left: { label: ReactNode; value: unknown };
  right: { label: ReactNode; value: unknown };
  className?: string;
  defaultShowUnchanged?: boolean;
}) {
  const [showUnchanged, setShowUnchanged] = useState(defaultShowUnchanged);

  const { rows, changeCount } = useMemo(() => {
    const l = flatten(left.value);
    const r = flatten(right.value);
    const keys = Array.from(new Set([...l.keys(), ...r.keys()])).sort();
    const all: DiffRow[] = keys.map((path) => {
      const lv = l.has(path) ? l.get(path)! : null;
      const rv = r.has(path) ? r.get(path)! : null;
      let status: DiffStatus;
      if (lv === null && rv !== null) status = 'added';
      else if (lv !== null && rv === null) status = 'removed';
      else if (lv !== rv) status = 'changed';
      else status = 'unchanged';
      return { path, left: lv, right: rv, status };
    });
    return { rows: all, changeCount: all.filter((x) => x.status !== 'unchanged').length };
  }, [left.value, right.value]);

  const visible = showUnchanged ? rows : rows.filter((r) => r.status !== 'unchanged');

  return (
    <div className={`overflow-hidden rounded-md border border-border-light ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-border-light bg-surface px-4 py-2 text-caption">
        <span className="text-slate">
          <span className="font-medium text-navy tnum">{changeCount}</span>{' '}
          {changeCount === 1 ? 'change' : 'changes'}
        </span>
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-slate">
          <input
            type="checkbox"
            checked={showUnchanged}
            onChange={(e) => setShowUnchanged(e.target.checked)}
            className="accent-[color:rgb(var(--accent))]"
          />
          Show unchanged
        </label>
      </div>
      <table className="w-full border-collapse text-body">
        <thead>
          <tr className="border-b border-border-light bg-surface/60 text-left text-micro uppercase tracking-wider text-slate">
            <th className="px-4 py-1.5 font-medium">Parameter</th>
            <th className="px-4 py-1.5 font-medium">{left.label}</th>
            <th className="px-4 py-1.5 font-medium">{right.label}</th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 ? (
            <tr>
              <td colSpan={3} className="px-4 py-8 text-center text-caption text-slate">
                No differences.
              </td>
            </tr>
          ) : (
            visible.map((row) => (
              <tr
                key={row.path}
                className={`border-b border-border-light last:border-b-0 ${STATUS_STYLE[row.status]}`}
              >
                <td className="px-4 py-1.5 font-mono text-caption text-slate">{row.path}</td>
                <td className="px-4 py-1.5 font-mono text-caption text-navy/90">
                  {row.left ?? <span className="text-slate-light">—</span>}
                </td>
                <td className="px-4 py-1.5 font-mono text-caption text-navy/90">
                  {row.right ?? <span className="text-slate-light">—</span>}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
