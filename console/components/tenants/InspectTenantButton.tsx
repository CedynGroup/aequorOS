"use client";

import { useEffect, useState } from "react";
import { Eye, ShieldAlert } from "lucide-react";
import { getTenant, startInspectorSession, toApiError } from "@/lib/api";
import { useInspector } from "@/lib/inspector";
import { inspectorHours } from "@/lib/inspector-duration";
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
 * path. The bank comes from the tenant's own record (GET /operator/v1/tenants/
 * {org}) when the modal opens, so an operator never types an institution id.
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
  // The examined bank, read from the tenant record when the modal first opens.
  const [bankId, setBankId] = useState<string | null>(null);
  const [bankLabel, setBankLabel] = useState<string | null>(null);
  const [bankLoading, setBankLoading] = useState(false);
  const [bankError, setBankError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || bankId !== null) return;
    let alive = true;
    setBankLoading(true);
    setBankError(null);
    getTenant(orgId)
      .then((tenant) => {
        if (!alive) return;
        setBankId(tenant.bank_id ?? null);
        setBankLabel(tenant.bank_name ?? null);
        setBankError(
          tenant.bank_id
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
  }, [open, orgId, bankId]);

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
              <p className="text-caption text-muted">Loading the institution…</p>
            ) : bankError ? (
              <p className="text-caption text-critical">{bankError}</p>
            ) : (
              <p className="text-body">
                {bankLabel ?? "This institution"}{" "}
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
