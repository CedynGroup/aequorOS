/**
 * AequorOS Operator Console — design-system barrel.
 *
 * The console's UI primitives now live as one-component-per-file modules under
 * `components/ui/`. This barrel re-exports all of them so the historical
 * `import { … } from '@/components/ui'` call sites keep working unchanged
 * (module resolution picks this file over the `ui/` directory).
 *
 * Dark-only, tokens from app/globals.css + tailwind.config.ts (a verbatim
 * duplicate of the bank dashboard's token layer). Add new primitives as files
 * under `ui/` and surface them here.
 */

// Status vocabulary + identity
export * from './ui/Chip';
export * from './ui/Identity';

// Async / feedback
export * from './ui/Skeleton';
export * from './ui/Feedback';
export * from './ui/QueryBoundary';

// Layout & structure
export * from './ui/Layout';
export * from './ui/Card';
export * from './ui/SectionCard';
export * from './ui/RunBadge';

// Forms & actions
export * from './ui/Button';
export * from './ui/Form';

// Data display
export * from './ui/DataTable';
export * from './ui/Kpi';
export * from './ui/Delta';
export * from './ui/StatusPill';
export * from './ui/LimitBar';
export * from './ui/Sparkline';
export * from './ui/ChartFrame';

// Navigation & disclosure
export * from './ui/Tabs';
export * from './ui/InfoTip';
export * from './ui/Overlay';
export * from './ui/Stepper';
export * from './ui/Toast';

// Governance surfaces
export * from './ui/VersionDiff';
export * from './ui/AuditTimeline';
