'use client';

/**
 * In-page secondary navigation (local state, not routes) for switching views
 * inside a single module tab — keeps the top ModuleTabs highlight intact.
 */

export type SubTab = { key: string; label: string };

export default function SubTabs({
  items,
  active,
  onChange,
}: {
  items: SubTab[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="border-b border-border-light">
      <nav className="-mb-px flex gap-1 overflow-x-auto" aria-label="Section views">
        {items.map((item) => {
          const isActive = item.key === active;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onChange(item.key)}
              className={`px-4 py-2.5 text-body font-medium border-b-2 whitespace-nowrap transition-colors ${
                isActive
                  ? 'border-action text-navy'
                  : 'border-transparent text-slate hover:text-navy hover:border-border'
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
