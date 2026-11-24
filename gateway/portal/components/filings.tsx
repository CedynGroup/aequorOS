import type { Channel, SubmissionRow, SubmissionState } from "@/lib/api/types";
import { channelLabel, softwareLabel, submissionStatus } from "@/lib/labels";
import { Chip, cx } from "./ui";

export function StatusChip({ state, size }: { state: SubmissionState; size?: "sm" | "md" }) {
  const status = submissionStatus(state);
  return (
    <Chip tone={status.tone} size={size}>
      {status.label}
    </Chip>
  );
}

export function ChannelCell({ channel, software }: { channel: Channel; software: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span>{channelLabel(channel)}</span>
      <span className="text-[12px] text-muted">{softwareLabel(software, channel)}</span>
    </div>
  );
}

/** "62 / 62 passed", "3 failed" for a rejected filing, "Running…" while validating. */
export function validationText(row: Pick<SubmissionRow, "validation" | "status">): { text: string; tone: "good" | "bad" | "pending" } {
  const v = row.validation;
  if (!v) return { text: row.status === "validating" || row.status === "received" ? "Running…" : "Not run", tone: "pending" };
  if (row.status === "rejected" && v.failed > 0) return { text: `${v.failed} failed`, tone: "bad" };
  return { text: `${v.passed} / ${v.total} passed`, tone: v.failed > 0 ? "bad" : "good" };
}

export function ValidationCell({ row }: { row: Pick<SubmissionRow, "validation" | "status"> }) {
  const { text, tone } = validationText(row);
  return <span className={cx("text-[13px]", tone === "good" && "font-medium text-ok", tone === "bad" && "font-semibold text-bad", tone === "pending" && "text-muted")}>{text}</span>;
}
