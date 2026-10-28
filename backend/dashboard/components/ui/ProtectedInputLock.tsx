import type { ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import {
  protectedDataMessages,
  type ProtectedDataState,
} from "@/lib/api/protectedData";

export default function ProtectedInputLock({
  state,
  children,
}: {
  state: ProtectedDataState;
  children: ReactNode;
}) {
  const locked = state !== "available";
  return (
    <fieldset
      disabled={locked}
      title={locked ? protectedDataMessages[state] : undefined}
      className={`relative min-w-0 ${locked ? "rounded-sm bg-surface text-slate [&_input]:bg-surface [&_input]:text-slate [&_input]:pr-9" : ""}`}
    >
      {children}
      {locked && (
        <LockKeyhole
          size={15}
          role="img"
          aria-label="Protected data locked"
          className="pointer-events-none absolute right-3 top-2 text-slate"
        />
      )}
    </fieldset>
  );
}
