"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { LockKeyhole } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useBankContext } from "@/components/shell/BankContext";
import { banksApi } from "@/lib/api/client";
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
  const bankId = bank?.id;
  const retry = useCallback(() => {
    if (
      !bankId ||
      ["available", "support"].includes(protectedDataState(bankId))
    )
      return;
    const hasRecoveryQuery = client
      .getQueryCache()
      .getAll()
      .some((query) => query.isActive() && isProtectedRecoveryQuery(query));
    if (!hasRecoveryQuery) {
      void banksApi.getBankRaw({ bankId }).catch(() => undefined);
    }
    void client.refetchQueries({
      type: "active",
      predicate: isProtectedRecoveryQuery,
    });
  }, [bankId, client]);
  useEffect(() => {
    if (!locked || state === "support") return;
    const timer = setInterval(retry, 5_000);
    return () => clearInterval(timer);
  }, [locked, retry, state]);

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
