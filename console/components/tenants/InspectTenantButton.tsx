"use client";

import { useEffect, useState } from "react";
import { Eye, ShieldAlert } from "lucide-react";
import {
  listTenants,
  startInspectorSession,
  toApiError,
  type OperatorTenant,
} from "@/lib/api";
import { useInspector } from "@/lib/inspector";
import { inspectorHours } from "@/lib/inspector-duration";
import { examinableBanks } from "@/lib/inspector-bank";
import { useMutation } from "@/lib/use-api";
import { Button, Field, Input, Modal, Select, Textarea } from "@/components/ui";

/**
 * Launches a time-boxed, audited Tenant Inspector session against one org.
 * Self-contained: renders the trigger Button, the confirmation Modal (reason
 * required, consent vs break-glass mode, TTL), calls startInspectorSession, and
 * pushes the result into the shell's inspector context so the un-dismissable
 * ImpersonationBanner appears. This console surface only OPENS a session — the
 * /admin/inspector page owns management.
 *
 * The session is bound to ONE bank — the institution it examines — because the
 * act-as-examiner token inherits that bank and the tenant API only serves the
 * filed registers an examination cites when the bound bank is the bank in the
 * path. The candidates come from the tenant board (GET /operator/v1/tenants,
 * which returns one row PER BANK) when the modal opens, so an operator never
 * types an institution id — and an organization with several banks offers each
 * of them. `GET /tenants/{org}` is not used for this: it answers with the
 * earliest-created bank, which would make a sibling bank unreachable.
 */

// consent = a read-only look with the tenant's knowledge; break_glass = an
// emergency read-only session without consent (admin-gated). Both are the
// backend's InspectorMode values, sent as-is.
type InspectMode = "consent" | "break_glass";

const TTL_PRESETS = [15, 30, 60];

export function InspectTenantButton({
  orgId,
  orgLabel,
}: {
  orgId: string;
  orgLabel?: string;
}) {
  const { setActive } = useInspector();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [supportReference, setSupportReference] = useState("");
  const [mode, setMode] = useState<InspectMode>("consent");
  const [ttl, setTtl] = useState(30);
  const [durationUnit, setDurationUnit] = useState<"hours" | "minutes">(
    "hours",
  );
  // The organization's banks, read from the tenant board when the modal opens.
  // `loadedForOrg` travels with the cache: the desktop entitlements page keeps
  // this button mounted while the selected organization changes, and a cache
  // keyed on nothing would offer the previous organization's banks.
  const [banks, setBanks] = useState<OperatorTenant[] | null>(null);
  const [loadedForOrg, setLoadedForOrg] = useState<string | null>(null);
  const [bankId, setBankId] = useState<string | null>(null);
  const [bankLoading, setBankLoading] = useState(false);
  const [bankError, setBankError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || (banks !== null && loadedForOrg === orgId)) return;
    let alive = true;
    setBankLoading(true);
    setBankError(null);
    listTenants()
      .then((response) => {
        if (!alive) return;
        const candidates = examinableBanks(response.tenants, orgId);
        setBanks(candidates);
        setLoadedForOrg(orgId);
        setBankId(candidates[0]?.bank_id ?? null);
        setBankError(
          candidates.length > 0
            ? null
            : "This organization has no bank yet, so there is no institution to examine.",
        );
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setBankError(toApiError(error).message);
      })
      .finally(() => {
        if (alive) setBankLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, orgId, banks, loadedForOrg]);

  const start = useMutation(startInspectorSession, {
    successMessage: "Inspector session started",
    errorContext: "Start inspector session",
    onSuccess: (session) => {
      setActive(session);
      setOpen(false);
      setReason("");
      setSupportReference("");
      setMode("consent");
      setTtl(30);
      setDurationUnit("hours");
    },
  });

  const reasonValid = reason.trim().length >= 3;
  const breakGlass = mode === "break_glass";

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        icon={<Eye size={14} aria-hidden />}
        onClick={() => setOpen(true)}
      >
        Inspect
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Open a Tenant Inspector session"
        description={`Cross-tenant access to ${orgLabel ?? orgId}. Every session is time-boxed and written to the operator audit log.`}
        size="md"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={start.loading}
            >
              Cancel
            </Button>
            <Button
              variant={breakGlass ? "danger" : "primary"}
              loading={start.loading}
              disabled={!reasonValid || !supportReference.trim() || !bankId}
              icon={
                breakGlass ? (
                  <ShieldAlert size={14} aria-hidden />
                ) : (
                  <Eye size={14} aria-hidden />
                )
              }
              onClick={() => {
                if (!bankId) return;
                void start.mutate({
                  organization_id: orgId,
                  bank_id: bankId,
                  reason: reason.trim(),
                  support_reference: supportReference.trim(),
                  mode,
                  ttl_minutes: ttl,
                });
              }}
            >
              {breakGlass ? "Break glass" : "Start read-only"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Examination bank" required>
            {bankLoading ? (
              <p className="text-caption text-muted">
                Loading the institutions…
              </p>
            ) : bankError ? (
              <p className="text-caption text-critical">{bankError}</p>
            ) : (banks?.length ?? 0) > 1 ? (
              // Several institutions in one organization: the session is bound
              // to ONE of them, so the operator picks which.
              <Select
                value={bankId ?? ""}
                onChange={(e) => setBankId(e.target.value)}
                aria-label="Examination bank"
              >
                {banks?.map((bank) => (
                  <option key={bank.bank_id} value={bank.bank_id ?? ""}>
                    {bank.bank_name ?? "Unnamed bank"} · {bank.bank_id}
                  </option>
                ))}
              </Select>
            ) : (
              <p className="text-body">
                {banks?.[0]?.bank_name ?? "This institution"}{" "}
                <span className="font-mono text-caption text-muted">
                  {bankId}
                </span>
              </p>
            )}
            <p className="mt-1 text-caption text-muted">
              The session is bound to this institution: its act-as-examiner
              token can read this bank&apos;s filed registers and no other
              institution&apos;s.
            </p>
          </Field>

          <Field label="Reason" required>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="e.g. Investigating a failed official run reported by the bank."
              invalid={reason.length > 0 && !reasonValid}
            />
          </Field>

          <Field label="Support reference" required>
            <Input
              value={supportReference}
              onChange={(e) => setSupportReference(e.target.value)}
              maxLength={200}
              placeholder="Ticket or incident reference"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Mode">
              <Select
                value={mode}
                onChange={(e) => setMode(e.target.value as InspectMode)}
              >
                <option value="consent">Consent (read-only)</option>
                <option value="break_glass">
                  Break glass (emergency, no consent)
                </option>
              </Select>
            </Field>
            <div className="grid grid-cols-2 items-start gap-2">
              <Field label="Session duration" htmlFor="tenant-inspect-duration">
                <Select
                  id="tenant-inspect-duration"
                  value={String(ttl)}
                  onChange={(e) => setTtl(Number(e.target.value))}
                >
                  {TTL_PRESETS.map((m) => (
                    <option key={m} value={m}>
                      {durationUnit === "hours"
                        ? `${inspectorHours(m)} h`
                        : `${m} min`}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Unit" htmlFor="tenant-inspect-duration-unit">
                <Select
                  id="tenant-inspect-duration-unit"
                  aria-label="Session duration unit"
                  value={durationUnit}
                  onChange={(e) =>
                    setDurationUnit(
                      e.target.value === "minutes" ? "minutes" : "hours",
                    )
                  }
                >
                  <option value="hours">Hours</option>
                  <option value="minutes">Minutes</option>
                </Select>
              </Field>
            </div>
          </div>

          {breakGlass && (
            <div className="rounded-md border border-critical/40 bg-critical-light p-3 text-caption text-critical">
              <p>
                <span className="font-medium">Break glass</span> opens a read /
                write session — you will be able to modify this tenant&apos;s
                data. Use only for an emergency the tenant cannot resolve
                themselves.
              </p>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
