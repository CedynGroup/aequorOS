import { LockKeyhole } from "lucide-react";
import { protectedDataMessages } from "@/lib/api/protectedData";

export function protectedPayloadReason(
  value: unknown,
): keyof typeof protectedDataMessages | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const reason = (value as Record<string, unknown>)._aequoros_protected_data;
  return reason === "revoked" ||
    reason === "unavailable" ||
    reason === "support"
    ? reason
    : null;
}

export default function ProtectedDataPlaceholder({
  value,
}: {
  value: unknown;
}) {
  const reason = protectedPayloadReason(value);
  if (!reason) return null;
  return (
    <div
      role="status"
      className="card flex items-start gap-3 border border-border bg-surface p-5"
    >
      <LockKeyhole size={18} aria-hidden />
      <div>
        <p className="font-medium text-navy">Protected data locked</p>
        <p className="mt-1 text-body text-slate">
          {protectedDataMessages[reason]}
        </p>
      </div>
    </div>
  );
}
