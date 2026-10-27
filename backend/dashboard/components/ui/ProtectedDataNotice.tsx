"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
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
  const bankId = bank?.id;
  const client = useQueryClient();
  const currentBank = useRef(bankId);
  const inFlight = useRef<AbortController | null>(null);
  useEffect(() => {
    currentBank.current = bankId;
    return () => {
      currentBank.current = undefined;
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [bankId]);
  const state = useSyncExternalStore(
    subscribeProtectedData,
    () => protectedDataState(bank?.id ?? ""),
    () => "available" as const,
  );
  const locked = state !== "available";
  const retry = useCallback(async () => {
    if (!bankId || currentBank.current !== bankId || inFlight.current) return;
    const current = protectedDataState(bankId);
    if (current === "available" || current === "support") return;
    const controller = new AbortController();
    inFlight.current = controller;
    const predicate = (query: Parameters<typeof isProtectedRecoveryQuery>[0]) =>
      query.queryKey.includes(bankId) &&
      !query.isDisabled() &&
      !query.isStatic() &&
      isProtectedRecoveryQuery(query);
    try {
      if (
        client.getQueryCache().findAll({ type: "active", predicate }).length
      ) {
        await client.refetchQueries(
          { type: "active", predicate },
          { cancelRefetch: false },
        );
      } else {
        await banksApi.getBankRaw({ bankId }, { signal: controller.signal });
      }
    } catch {
      return;
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, [bankId, client]);
  useEffect(() => {
    if (!bankId || !locked || state === "support") {
      inFlight.current?.abort();
      return;
    }
    const timer = setInterval(() => {
      void retry();
    }, 5_000);
    return () => clearInterval(timer);
  }, [bankId, locked, state, retry]);

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
