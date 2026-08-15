import { StatusPill } from '@/components/ui';
import { roleLabel, roleTone } from './util';

/** An operator role as a status pill, colored by ladder position. */
export function RoleBadge({ role }: { role: string }) {
  return <StatusPill tone={roleTone(role)}>{roleLabel(role)}</StatusPill>;
}
