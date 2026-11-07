"use client";

/**
 * Governance & Board sign-off for an enterprise-stress run (docs/stress.md §3.8,
 * BoG Guideline ¶20, ¶57-63, ¶67(b)(c)). Surfaces the Phase-5 governed sign-off:
 * the preparer captures the scenario narrative + assumptions rationale (¶67(a)(b),
 * ¶45), submits for attestation, and the Board (a different user — maker≠checker)
 * attests with a credibility rationale + challenge record (¶20). Only an attested
 * run may be filed as the ICAAP Appendix II stress return.
 */

import { useEffect, useState } from "react";
import { ShieldCheck, Send, PenLine } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill, { type StatusTone } from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { fmtDateUTC } from "@/lib/api/values";
import { ApiError } from "@/lib/api/client";
import { PermissionButton } from "@/components/ui/PermissionButton";
import { useStressSigningEligibility } from "@/lib/api/signingAccess";
import { useOrganizationUsers } from "@/lib/api/hooks";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { hasAccountDirectoryAuthority } from "@/lib/api/accountAdministration";
import {
  useStressAuthority,
  stressPermissionReason,
} from "./useStressAuthority";
import {
  useBankStressSignoffs,
  useStressSignoff,
  useCreateStressSignoff,
  useUpdateStressSignoff,
  useSubmitStressSignoff,
  useAttestStressSignoff,
  useAttestStressSignoffWithHeldAuthorization,
  useStressSignoffStepUp,
  useWithdrawStressSignoff,
} from "./hooks";
import type {
  EnterpriseStressRead,
  StressSignoffRead,
  StressSignoffStatus,
} from "./types";

const STATUS_TONE: Record<StressSignoffStatus, StatusTone> = {
  draft: "slate",
  pending_attestation: "amber",
  attested: "success",
  withdrawn: "slate",
};
const STATUS_LABEL: Record<StressSignoffStatus, string> = {
  draft: "Draft",
  pending_attestation: "Pending Board attestation",
  attested: "Board-attested",
  withdrawn: "Withdrawn",
};

const inputCls =
  "w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-caption text-navy placeholder:text-slate-light focus:border-action focus:outline-hidden";
const btnCls =
  "inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-1.5 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50";
const btnGhost =
  "inline-flex items-center gap-1.5 rounded-md border border-border-light px-3 py-1.5 text-caption text-navy hover:bg-slate-50 disabled:opacity-50";

function errText(e: unknown): string {
  return e instanceof ApiError
    ? e.message
    : e instanceof Error
      ? e.message
      : String(e);
}

function Field({
  label,
  hint,
  value,
  onChange,
  readOnly,
  rows = 3,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange?: (v: string) => void;
  readOnly?: boolean;
  rows?: number;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-micro font-medium uppercase tracking-wide text-slate">
        {label}
      </span>
      {hint ? (
        <span className="block text-micro text-slate-light">{hint}</span>
      ) : null}
      <textarea
        className={inputCls}
        rows={rows}
        value={value}
        readOnly={readOnly}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
      />
    </label>
  );
}

function CreateForm({
  bankId,
  runId,
}: {
  bankId: string | undefined;
  runId: string;
}) {
  const create = useCreateStressSignoff(bankId);
  const authority = useStressAuthority();
  const [narrative, setNarrative] = useState("");
  const [rationale, setRationale] = useState("");
  const [methodology, setMethodology] = useState("");
  const [reason, setReason] = useState("");
  const canSubmit =
    authority.can("create", bankId) &&
    narrative.trim() &&
    rationale.trim() &&
    reason.trim();
  return (
    <div className="space-y-3 p-5">
      <p className="text-caption text-slate">
        Record the sign-off for this run: the risks, exposures and macro
        assumptions covered, and the justification of the methodology and any
        expert-judgement overlays.
      </p>
      <Field
        label="Scenario narrative"
        hint="¶67(a)(b): risks / exposures / entities covered + macro conditions and assumptions."
        value={narrative}
        onChange={setNarrative}
      />
      <Field
        label="Assumptions rationale"
        hint="¶67(b), ¶45: justification of the assumptions and of expert-judgement overlays."
        value={rationale}
        onChange={setRationale}
      />
      <Field
        label="Methodology summary (optional)"
        hint="¶67(c): the methodologies used, including any material changes from the previous exercise."
        value={methodology}
        onChange={setMethodology}
        rows={2}
      />
      <input
        className={inputCls}
        placeholder="Reason (audit)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {create.error ? (
        <p className="text-caption text-critical">{errText(create.error)}</p>
      ) : null}
      <PermissionButton
        type="button"
        className={btnCls}
        disabled={!canSubmit || create.isPending}
        reason={
          authority.can("create", bankId)
            ? undefined
            : stressPermissionReason("create")
        }
        onClick={() =>
          create.mutate({
            runId,
            scenarioNarrative: narrative,
            assumptionsRationale: rationale,
            methodologySummary: methodology || null,
            reason,
          })
        }
      >
        <PenLine size={14} /> Create sign-off (draft)
      </PermissionButton>
    </div>
  );
}

function AttestForm({ signoff }: { signoff: StressSignoffRead }) {
  const signing = useStressSigningEligibility(
    signoff.bankId,
    signoff.attestationRecipientUserId,
  );
  const stepUp = useStressSignoffStepUp(signoff.bankId);
  const attest = useAttestStressSignoff(signoff.bankId);
  const attestWithHeld = useAttestStressSignoffWithHeldAuthorization(
    signoff.bankId,
  );
  const [credibility, setCredibility] = useState("");
  const [challenge, setChallenge] = useState("");
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [ssoReady, setSsoReady] = useState(false);
  const [ssoError, setSsoError] = useState("");
  const stashKey = `stress-signoff-step-up:${signoff.id}`;
  const hasRationale = Boolean(credibility.trim() && reason.trim());
  const canAttest =
    hasRationale &&
    Boolean(password.trim() || ssoReady) &&
    signing.allowed;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("stepUp");
    if (!outcome) return;
    params.delete("stepUp");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}`,
    );
    if (outcome !== "ready") {
      setSsoError(
        "Single sign-on did not confirm this signature. Try again or use your password.",
      );
      return;
    }
    setSsoReady(true);
    try {
      const saved = window.sessionStorage.getItem(stashKey);
      if (saved) {
        const values: unknown = JSON.parse(saved);
        if (values && typeof values === "object") {
          if ("credibility" in values && typeof values.credibility === "string") {
            setCredibility(values.credibility);
          }
          if ("challenge" in values && typeof values.challenge === "string") {
            setChallenge(values.challenge);
          }
          if ("reason" in values && typeof values.reason === "string") {
            setReason(values.reason);
          }
        }
        window.sessionStorage.removeItem(stashKey);
      }
    } catch {
      // Storage is only a convenience across the redirect; the signer can retype.
    }
  }, [stashKey]);

  if (!signing.allowed) {
    return (
      <p
        role="status"
        className="rounded-md border border-border-light bg-slate-50 p-4 text-caption text-warning"
      >
        {signing.reason}
      </p>
    );
  }

  const pending =
    stepUp.isPending || attest.isPending || attestWithHeld.isPending;
  const attestation = {
    expectedSigningDigest: signoff.signingDigest,
    credibilityRationale: credibility,
    boardChallenge: challenge || null,
    reason,
  };
  const onSubmit = () => {
    if (!canAttest || pending) return;
    void (async () => {
      if (ssoReady) {
        await attestWithHeld.mutateAsync({ signoffId: signoff.id, body: attestation });
      } else {
        const granted = await stepUp.mutateAsync({
          signoffId: signoff.id,
          body: { password },
        });
        await attest.mutateAsync({
          signoffId: signoff.id,
          body: { authorizationToken: granted.authorizationToken, ...attestation },
        });
      }
      setPassword("");
      setSsoReady(false);
    })().catch(() => undefined);
  };
  const onSsoStepUp = () => {
    if (!hasRationale || pending) return;
    try {
      window.sessionStorage.setItem(
        stashKey,
        JSON.stringify({ credibility, challenge, reason }),
      );
    } catch {
      // The form remains usable after SSO even when browser storage is blocked.
    }
    const returnTo = new URL(window.location.href);
    returnTo.searchParams.delete("stepUp");
    const start = new URL("/api/attestation/step-up/start", window.location.origin);
    start.searchParams.set("bankId", signoff.bankId);
    start.searchParams.set("signoffId", signoff.id);
    start.searchParams.set(
      "returnTo",
      `${returnTo.pathname}${returnTo.search}`,
    );
    window.location.assign(start);
  };

  return (
    <div
      role="group"
      aria-label="Board attestation"
      className="space-y-3 rounded-md border border-border-light bg-slate-50 p-4"
    >
      <p className="text-caption font-medium text-navy">
        Board attestation (¶20)
      </p>
      <p className="text-micro text-slate">
        The named Signer must differ from the preparer. Re-authentication mints
        a one-shot authorization for this exact frozen signing digest.
      </p>
      <p className="text-micro text-slate">
        Signing digest: <code>{signoff.signingDigest}</code>
      </p>
      <Field
        label="Credibility rationale"
        hint="¶20: the Board's rationale for the credibility of the framework + results."
        value={credibility}
        onChange={setCredibility}
      />
      <Field
        label="Board challenge (optional)"
        value={challenge}
        onChange={setChallenge}
        rows={2}
      />
      <input
        className={inputCls}
        placeholder="Reason (audit)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <label className="block space-y-1">
        <span className="text-micro font-medium uppercase tracking-wide text-slate">
          Your password
        </span>
        <input
          type="password"
          autoComplete="current-password"
          className={inputCls}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {ssoReady ? (
        <p role="status" className="text-caption text-success">
          Single sign-on confirmed this signature. Review your rationale and attest.
        </p>
      ) : null}
      {ssoError || stepUp.error || attest.error || attestWithHeld.error ? (
        <p className="text-caption text-critical">
          {ssoError || errText(stepUp.error ?? attest.error ?? attestWithHeld.error)}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={btnCls}
          disabled={!canAttest || pending}
          onClick={onSubmit}
        >
          <ShieldCheck size={14} />
          {stepUp.isPending
            ? "Confirming identity…"
            : attest.isPending || attestWithHeld.isPending
              ? "Attesting…"
              : ssoReady
                ? "Attest with single sign-on"
                : "Confirm identity and attest"}
        </button>
        {!ssoReady ? (
          <button
            type="button"
            className={btnGhost}
            disabled={!hasRationale || !signing.allowed || pending}
            onClick={onSsoStepUp}
          >
            Use single sign-on
          </button>
        ) : null}
      </div>
    </div>
  );
}

function SignoffDetail({
  bankId,
  signoffId,
}: {
  bankId: string | undefined;
  signoffId: string;
}) {
  const detail = useStressSignoff(bankId, signoffId);
  const submit = useSubmitStressSignoff(bankId);
  const withdraw = useWithdrawStressSignoff(bankId);
  const update = useUpdateStressSignoff(bankId);
  const [reason, setReason] = useState("");

  return (
    <QueryBoundary
      isLoading={detail.isLoading}
      error={detail.error}
      onRetry={() => detail.refetch()}
    >
      {detail.data ? (
        <SignoffBody
          s={detail.data}
          reason={reason}
          setReason={setReason}
          onSubmit={(attestationRecipientUserId) =>
            submit.mutate({
              signoffId,
              body: { attestationRecipientUserId, reason },
            })
          }
          onWithdraw={() => withdraw.mutate({ signoffId, body: { reason } })}
          onSaveDraft={(body) =>
            update.mutate({ signoffId, body: { ...body, reason } })
          }
          busy={submit.isPending || withdraw.isPending || update.isPending}
          actionError={errText(
            submit.error ?? withdraw.error ?? update.error ?? "",
          )}
        />
      ) : null}
    </QueryBoundary>
  );
}

function SignoffBody({
  s,
  reason,
  setReason,
  onSubmit,
  onWithdraw,
  onSaveDraft,
  busy,
  actionError,
}: {
  s: StressSignoffRead;
  reason: string;
  setReason: (v: string) => void;
  onSubmit: (attestationRecipientUserId: string) => void;
  onWithdraw: () => void;
  onSaveDraft: (body: {
    scenarioNarrative?: string;
    assumptionsRationale?: string;
    methodologySummary?: string | null;
  }) => void;
  busy: boolean;
  actionError: string;
}) {
  const authority = useStressAuthority();
  const { effectiveAuthority } = useUserProfile();
  const canReadDirectory = hasAccountDirectoryAuthority(effectiveAuthority);
  const usersQuery = useOrganizationUsers(canReadDirectory);
  const canEdit = authority.can("edit", s.bankId);
  const isDraft = s.status === "draft";
  const [narrative, setNarrative] = useState(s.scenarioNarrative);
  const [rationale, setRationale] = useState(s.assumptionsRationale);
  const [methodology, setMethodology] = useState(s.methodologySummary ?? "");
  const [recipientUserId, setRecipientUserId] = useState("");
  const users = (usersQuery.data?.users ?? []).filter((user) => user.isActive);
  const canSubmit = Boolean(canEdit && reason.trim() && recipientUserId);

  return (
    <div className="space-y-4 p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusPill tone={STATUS_TONE[s.status]}>
            {STATUS_LABEL[s.status]}
          </StatusPill>
          <span className="text-micro text-slate-light">
            v{s.version} · {s.scenarioCode}
          </span>
        </div>
        {s.attestedAt ? (
          <span className="text-micro text-slate-light">
            attested {fmtDateUTC(new Date(s.attestedAt))}
          </span>
        ) : null}
      </div>

      {s.status === "attested" ? (
        <div className="rounded-md border border-border-light bg-slate-50 p-3 text-caption text-navy">
          This run is <strong>Board-attested</strong> and may be filed as the
          ICAAP Appendix II stress return. The governance record below is
          immutable.
        </div>
      ) : null}

      <Field
        label="Scenario narrative"
        value={isDraft ? narrative : s.scenarioNarrative}
        onChange={isDraft ? setNarrative : undefined}
        readOnly={!isDraft || !canEdit}
      />
      <Field
        label="Assumptions rationale"
        value={isDraft ? rationale : s.assumptionsRationale}
        onChange={isDraft ? setRationale : undefined}
        readOnly={!isDraft || !canEdit}
      />
      <Field
        label="Methodology summary"
        value={isDraft ? methodology : (s.methodologySummary ?? "—")}
        onChange={isDraft ? setMethodology : undefined}
        readOnly={!isDraft || !canEdit}
        rows={2}
      />

      {isDraft ? (
        <label className="block space-y-1">
          <span className="text-micro font-medium uppercase tracking-wide text-slate">
            Board sign-off recipient
          </span>
          {canReadDirectory ? (
            <select
              aria-label="Board sign-off recipient"
              className={inputCls}
              value={recipientUserId}
              onChange={(event) => setRecipientUserId(event.target.value)}
            >
              <option value="">Select a named Signer…</option>
              {users.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.displayName ?? user.email}
                  {user.jobTitle ? ` — ${user.jobTitle}` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label="Board sign-off recipient"
              className={inputCls}
              placeholder="Recipient user ID"
              value={recipientUserId}
              onChange={(event) => setRecipientUserId(event.target.value.trim())}
            />
          )}
          <span className="block text-micro text-slate-light">
            {canReadDirectory
              ? "Select the named Signer. "
              : "Enter the recipient's user ID; directory read authority is not required. "}
            The server checks the recipient&apos;s exact-bank Signer and source
            read grants, and refuses self-attestation by the preparer.
          </span>
        </label>
      ) : s.attestationRecipientUserId ? (
        <p className="text-caption text-slate">
          Named recipient: {users.find(
            (user) => user.id === s.attestationRecipientUserId,
          )?.displayName ?? s.attestationRecipientUserId}
        </p>
      ) : null}

      {s.status === "attested" || s.status === "pending_attestation" ? (
        <>
          <Field
            label="Board credibility rationale"
            value={s.credibilityRationale ?? "—"}
            readOnly
            rows={2}
          />
          {s.boardChallenge ? (
            <Field
              label="Board challenge"
              value={s.boardChallenge}
              readOnly
              rows={2}
            />
          ) : null}
        </>
      ) : null}

      {s.status !== "attested" ? (
        <input
          className={inputCls}
          placeholder="Reason (audit)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      ) : null}
      {actionError ? (
        <p className="text-caption text-critical">{actionError}</p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {isDraft ? (
          <>
            <PermissionButton
              type="button"
              className={btnGhost}
              disabled={busy || !reason.trim() || !canEdit}
              reason={canEdit ? undefined : stressPermissionReason("edit")}
              onClick={() =>
                onSaveDraft({
                  scenarioNarrative: narrative,
                  assumptionsRationale: rationale,
                  methodologySummary: methodology || null,
                })
              }
            >
              Save draft
            </PermissionButton>
            <PermissionButton
              type="button"
              className={btnCls}
              disabled={busy || !canSubmit}
              reason={
                canEdit
                  ? recipientUserId
                    ? undefined
                    : "Choose the named Signer who must attest."
                  : stressPermissionReason("edit")
              }
              onClick={() => onSubmit(recipientUserId)}
            >
              <Send size={14} /> Submit for Board attestation
            </PermissionButton>
          </>
        ) : null}
        {s.status === "pending_attestation" ? (
          <PermissionButton
            type="button"
            className={btnGhost}
            disabled={busy || !reason.trim() || !canEdit}
            reason={canEdit ? undefined : stressPermissionReason("edit")}
            onClick={onWithdraw}
          >
            Withdraw to draft
          </PermissionButton>
        ) : null}
      </div>

      {s.status === "pending_attestation" ? (
        <AttestForm signoff={s} />
      ) : null}
    </div>
  );
}

export default function SignoffPanel({
  run,
  bankId,
}: {
  run: Pick<EnterpriseStressRead, "run_id"> | null;
  bankId: string | undefined;
}) {
  const signoffs = useBankStressSignoffs(bankId);

  if (!run) {
    return (
      <SectionCard
        title="Governance & sign-off"
        subtitle="Board attestation of the stress run (¶20)"
      >
        <div className="p-5">
          <EmptyState
            Icon={ShieldCheck}
            title="Open a run to record its sign-off"
            description="Run an enterprise stress test and open it from the registry — its Board sign-off and attestation are captured here and gate the ICAAP stress return."
          />
        </div>
      </SectionCard>
    );
  }

  const existing = (signoffs.data ?? []).find((x) => x.runId === run.run_id);

  return (
    <SectionCard
      title="Governance & sign-off"
      subtitle="Analyst/CRO narrative → Board attestation (maker ≠ checker) — gates the ICAAP return"
      noPadding
    >
      <QueryBoundary
        isLoading={signoffs.isLoading}
        error={signoffs.error}
        onRetry={() => signoffs.refetch()}
      >
        {existing ? (
          <SignoffDetail bankId={bankId} signoffId={existing.id} />
        ) : (
          <CreateForm bankId={bankId} runId={run.run_id} />
        )}
      </QueryBoundary>
    </SectionCard>
  );
}
