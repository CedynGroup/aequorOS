"use client";

import { useEffect, useSyncExternalStore } from "react";
import { LockKeyhole } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useBankContext } from "@/components/shell/BankContext";
import {
  isProtectedRecoveryQuery,
  protectedDataMessages,
  protectedDataState,
  subscribeProtectedData,
} from "@/lib/api/protectedData";

export default function ProtectedDataNotice() {
  const { bank } = useBankContext();
  const client = useQueryClient();
  const state = useSyncExternalStore(
    subscribeProtectedData,
    () => protectedDataState(bank?.id ?? ""),
    () => "available" as const,
  );
  const locked = state !== "available";
  const retry = () => {
    void client.refetchQueries({
      type: "active",
      predicate: isProtectedRecoveryQuery,
    });
  };
  useEffect(() => {
    if (!locked || state === "support") return;
    const timer = setInterval(() => {
      void client.refetchQueries({
        type: "active",
        predicate: isProtectedRecoveryQuery,
      });
    }, 5_000);
    return () => clearInterval(timer);
  }, [client, locked, state]);

  if (!locked) return null;
  return (
    <div
      role="status"
      className="flex items-center gap-3 border-b border-border bg-surface px-6 py-3 text-body text-navy"
    >
      <LockKeyhole size={18} aria-hidden />
      <p className="flex-1">{protectedDataMessages[state]}</p>
      {state !== "support" && (
        <button
          type="button"
          onClick={retry}
          className="rounded border border-border px-3 py-1"
        >
          Retry
        </button>
      )}
    </div>
  );
}
