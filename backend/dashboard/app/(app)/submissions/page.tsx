import { redirect } from 'next/navigation';

/**
 * Regulatory Reporting hub → Returns.
 *
 * The hub used to land on the Calendar, because the deadline board happened to
 * be the index route. That is the wrong first screen: the calendar tells you
 * when something is due, but the work — generate → validate → approve → export
 * → submit — happens in Returns, and every other tab is reachable from there.
 * A preparer opening Regulatory Reporting wants the workspace, not the diary.
 *
 * A redirect rather than a moved sidebar link, so existing bookmarks and the
 * command palette's `/submissions` entry land on the workspace too. The
 * calendar keeps a real URL of its own at /submissions/calendar.
 */
export default function RegulatoryReportingHub() {
  redirect('/submissions/returns');
}
