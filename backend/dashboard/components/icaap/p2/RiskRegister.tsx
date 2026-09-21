"use client";

/**
 * The ICAAP risk register: every risk category the framework names, plus the
 * bank's own emerging risks.
 *
 * The register is LAZY on the server — a category the bank has never assessed
 * has no row, and the API returns it with nulls rather than defaults. The
 * screen shows that honestly: "Not assessed", never a score of 0 and never an
 * implied "not material".
 *
 * D-024: the likelihood and impact options come from the matrix payload's own
 * levels, the verdict comes from the server, and the materiality threshold is
 * printed by `MaterialityMatrix` from `materialMinScore`/`materialMinImpact`.
 * Nothing here compares a score with anything.
 *
 * Concurrency: every save carries the row revision it read. The API answers 409
 * `row_rev_conflict` when someone else moved the row first, and the screen
 * surfaces that as a reload prompt rather than overwriting their assessment.
 */

import { useState } from "react";
import { AlertTriangle, Plus, RefreshCw } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { isApiError } from "@/lib/api/client";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  useCreateIcaapCustomRisk,
  useIcaapRisks,
  usePutIcaapRisk,
  type IcaapMaterialityMatrix,
  type IcaapRiskPut,
  type IcaapRisk,
} from "@/lib/api/icaapRiskCapital";
import MaterialityMatrix from "./MaterialityMatrix";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import ParameterProvenance from "./ParameterProvenance";
import {
  ICON_SM,
  RATIONALE_MAX,
  REASON_MAX,
  ROWS_MEDIUM,
  TITLE_MAX,
} from "./display";
import {
  NOT_ASSESSED,
  componentMethodSummary,
  fmtScore,
  verdictLabel,
  verdictSourceLabel,
  verdictTone,
} from "./labels";

export default function RiskRegister({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canEdit = scope.capitalEdit === true;
  const registerQuery = useIcaapRisks(bankId, cycleId);
  const [editing, setEditing] = useState<IcaapRisk | null>(null);
  const [addingRisk, setAddingRisk] = useState(false);

  const register = registerQuery.data;
  const risks = register?.risks ?? [];
  const parameters = register?.parameters ?? [];

  // The routes may not be served for this institution yet. That is a state, not
  // a failure, and it is decided BEFORE any payload is read.
  const unavailable = p2UnavailableNotice(registerQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Risk register" message={unavailable} />;
  }

  return (
    <QueryBoundary
      isLoading={registerQuery.isLoading}
      error={registerQuery.error}
      onRetry={() => void registerQuery.refetch()}
      contained
    >
      {register && (
        <div className="space-y-4">
          <SectionCard
            title="Risk register"
            subtitle="Every risk the framework names, with the bank's own assessment."
            actions={
              canEdit ? (
                <SecondaryButton onClick={() => setAddingRisk(true)}>
                  <Plus size={ICON_SM} aria-hidden />
                  Add emerging risk
                </SecondaryButton>
              ) : undefined
            }
            noPadding
          >
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="border-b border-border-light text-caption text-slate">
                    <th scope="col" className="px-4 py-2 text-left">
                      Risk
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Likelihood
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Impact
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Inherent rating
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Material?
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Quantified in Pillar 2 by
                    </th>
                    <th scope="col" className="px-4 py-2 text-left">
                      Owner
                    </th>
                    <th scope="col" className="px-4 py-2 text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {risks.map((risk) => (
                    <tr
                      key={risk.riskKey}
                      className="border-b border-border-light/60 align-top"
                    >
                      <td className="px-4 py-3">
                        <span className="font-medium text-navy">
                          {risk.title}
                        </span>
                        {risk.isCustom && (
                          <span className="ml-2 text-caption text-slate">
                            Bank-defined
                          </span>
                        )}
                        {!risk.thresholdsCurrent && (
                          <p className="mt-1 flex items-center gap-1 text-caption text-warning">
                            <AlertTriangle size={ICON_SM} aria-hidden />
                            The governed thresholds changed after this
                            assessment. Re-open it to confirm the verdict.
                          </p>
                        )}
                        {risk.materialityRationale && (
                          <p className="mt-1 text-caption text-slate">
                            {risk.materialityRationale}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 tnum">
                        {fmtScore(risk.likelihoodScore)}
                      </td>
                      <td className="px-4 py-3 tnum">
                        {fmtScore(risk.impactScore)}
                      </td>
                      <td className="px-4 py-3">
                        {risk.ratingLabel ?? NOT_ASSESSED}
                        {risk.materialityScore !== null && (
                          <span className="ml-1 text-caption text-slate tnum">
                            ({risk.materialityScore})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <StatusPill tone={verdictTone(risk.verdict)}>
                          {verdictLabel(risk.verdict)}
                        </StatusPill>
                        {verdictSourceLabel(risk.verdictSource) && (
                          <p className="mt-1 text-caption text-slate">
                            {verdictSourceLabel(risk.verdictSource)}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {componentMethodSummary(risk.components ?? [])}
                        {(risk.components ?? []).length > 0 && (
                          <ul className="mt-1 space-y-0.5 text-caption text-slate">
                            {(risk.components ?? []).map((component) => (
                              <li key={component.componentKey}>
                                {component.componentKey}
                                {component.methodStatus
                                  ? ` — ${component.methodStatus}`
                                  : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {risk.ownerFunction ?? NOT_ASSESSED}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {canEdit && (
                          <SecondaryButton onClick={() => setEditing(risk)}>
                            {risk.rowRev === 0 ? "Assess" : "Update"}
                          </SecondaryButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SectionCard>

          <MaterialityMatrix matrix={register.matrix} risks={risks} />

          {parameters.length > 0 && (
            <SectionCard
              title="Where these thresholds come from"
              subtitle="Every threshold on this tab is a control-plane value, shown with its citation."
            >
              <ParameterProvenance uses={parameters} />
            </SectionCard>
          )}
        </div>
      )}

      {editing && register && (
        <AssessRiskDialog
          bankId={bankId}
          cycleId={cycleId}
          risk={editing}
          matrix={register.matrix}
          onClose={() => setEditing(null)}
          onReload={() => {
            setEditing(null);
            void registerQuery.refetch();
          }}
        />
      )}

      {addingRisk && (
        <AddCustomRiskDialog
          bankId={bankId}
          cycleId={cycleId}
          categories={categoriesOf(register)}
          onClose={() => setAddingRisk(false)}
        />
      )}
    </QueryBoundary>
  );
}

/** The framework's categories, taken from the rows the server already returned. */
function categoriesOf(
  register: { risks?: readonly IcaapRisk[] } | undefined,
): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const risk of register?.risks ?? []) {
    if (!risk.isCustom && !seen.has(risk.categoryKey)) {
      seen.set(risk.categoryKey, risk.title);
    }
  }
  return [...seen].map(([key, label]) => ({ key, label }));
}

// ---------------------------------------------------------------------------
// Assess / update one risk
// ---------------------------------------------------------------------------

function AssessRiskDialog({
  bankId,
  cycleId,
  risk,
  matrix,
  onClose,
  onReload,
}: {
  bankId: string;
  cycleId: string;
  risk: IcaapRisk;
  matrix: IcaapMaterialityMatrix | null | undefined;
  onClose: () => void;
  onReload: () => void;
}) {
  const mutation = usePutIcaapRisk(bankId, cycleId);
  const likelihoodLevels = matrix?.likelihoodLevels ?? [];
  const impactLevels = matrix?.impactLevels ?? [];
  const [likelihood, setLikelihood] = useState<string>(
    risk.likelihoodScore === null ? "" : String(risk.likelihoodScore),
  );
  const [impact, setImpact] = useState<string>(
    risk.impactScore === null ? "" : String(risk.impactScore),
  );
  const [rationale, setRationale] = useState(risk.materialityRationale ?? "");
  const [owner, setOwner] = useState(risk.ownerFunction ?? "");
  const [overrideVerdict, setOverrideVerdict] = useState<string>(
    risk.verdictSource === "override" ? (risk.verdict ?? "") : "",
  );
  const [overrideReason, setOverrideReason] = useState(
    risk.overrideReason ?? "",
  );
  const [reason, setReason] = useState("");

  const conflict =
    isApiError(mutation.error) &&
    (mutation.error.details as { error_code?: string } | undefined)
      ?.error_code === "row_rev_conflict";

  const submit = () => {
    const payload: IcaapRiskPut = {
      baseRev: risk.rowRev === 0 ? null : risk.rowRev,
      likelihoodScore: likelihood === "" ? null : Number(likelihood),
      impactScore: impact === "" ? null : Number(impact),
      materialityRationale: rationale.trim() === "" ? null : rationale.trim(),
      ownerFunction: owner.trim() === "" ? null : owner.trim(),
      override:
        overrideVerdict === ""
          ? null
          : (overrideVerdict as "material" | "not_material"),
      overrideReason:
        overrideReason.trim() === "" ? null : overrideReason.trim(),
      reason: reason.trim(),
    };
    mutation.mutate(
      { riskKey: risk.riskKey, payload },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <Dialog
      title={risk.title}
      description="The verdict is decided by the server from the governed thresholds. An override needs a reason and is recorded as the bank's own judgement."
      onClose={onClose}
      wide
      footer={
        conflict ? (
          <PrimaryButton onClick={onReload}>
            <RefreshCw size={ICON_SM} aria-hidden />
            Reload their version
          </PrimaryButton>
        ) : (
          <>
            <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
            <PrimaryButton
              onClick={submit}
              disabled={mutation.isPending || reason.trim() === ""}
            >
              {mutation.isPending ? "Saving…" : "Save assessment"}
            </PrimaryButton>
          </>
        )
      }
    >
      <div className="space-y-3">
        {conflict && (
          <p className="card border-l-4 border-l-warning bg-warning-light/40 p-3 text-body text-navy/80">
            Someone else assessed this risk while this form was open. Nothing
            has been overwritten — reload their version and apply your change on
            top of it.
          </p>
        )}
        {mutation.isError && !conflict && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <FieldLabel label="Likelihood">
            <select
              aria-label="Likelihood"
              value={likelihood}
              onChange={(event) => setLikelihood(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            >
              <option value="">Not assessed</option>
              {likelihoodLevels.map((level) => (
                <option key={level.score} value={String(level.score)}>
                  {level.label}
                </option>
              ))}
            </select>
          </FieldLabel>
          <FieldLabel label="Impact">
            <select
              aria-label="Impact"
              value={impact}
              onChange={(event) => setImpact(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            >
              <option value="">Not assessed</option>
              {impactLevels.map((level) => (
                <option key={level.score} value={String(level.score)}>
                  {level.label}
                </option>
              ))}
            </select>
          </FieldLabel>
        </div>

        <FieldLabel
          label="Why this rating"
          hint="What the assessment rests on. A reviewer reads this before the score."
        >
          <textarea
            value={rationale}
            maxLength={RATIONALE_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setRationale(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <p className="text-caption text-slate">
          How this risk is quantified beyond Pillar 1 is decided in the Pillar 2
          register, not here: an item there carries the method, its inputs and
          its own approval.
        </p>

        <FieldLabel label="Owner (the function accountable for this risk)">
          <input
            value={owner}
            maxLength={TITLE_MAX}
            onChange={(event) => setOwner(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel
          label="Override the matrix verdict"
          hint="Leave unset to accept the matrix. An override is the bank's own judgement and must say why."
        >
          <select
            aria-label="Override the matrix verdict"
            value={overrideVerdict}
            onChange={(event) => setOverrideVerdict(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          >
            <option value="">Accept the matrix verdict</option>
            <option value="material">Treat as material</option>
            <option value="not_material">Treat as not material</option>
          </select>
        </FieldLabel>

        {overrideVerdict !== "" && (
          <FieldLabel label="Reason for the override">
            <textarea
              value={overrideReason}
              maxLength={RATIONALE_MAX}
              rows={ROWS_MEDIUM}
              onChange={(event) => setOverrideReason(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
        )}

        <FieldLabel
          label="Reason for this change"
          hint="Recorded in the audit trail."
        >
          <input
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Add an emerging risk
// ---------------------------------------------------------------------------

function AddCustomRiskDialog({
  bankId,
  cycleId,
  categories,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  categories: { key: string; label: string }[];
  onClose: () => void;
}) {
  const mutation = useCreateIcaapCustomRisk(bankId, cycleId);
  const [title, setTitle] = useState("");
  const [categoryKey, setCategoryKey] = useState(categories[0]?.key ?? "");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title="Add an emerging risk"
      description="A risk the bank has identified that the framework's categories do not name. It joins the register and the matrix like any other."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              title.trim() === "" ||
              categoryKey === "" ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                {
                  title: title.trim(),
                  categoryKey,
                  description:
                    description.trim() === "" ? null : description.trim(),
                  ownerFunction: owner.trim() === "" ? null : owner.trim(),
                  reason: reason.trim(),
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Adding…" : "Add risk"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}
        <FieldLabel label="Name of the risk">
          <input
            value={title}
            maxLength={TITLE_MAX}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel
          label="Closest framework category"
          hint="Where this risk is reported in the return."
        >
          <select
            aria-label="Closest framework category"
            value={categoryKey}
            onChange={(event) => setCategoryKey(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          >
            {categories.map((category) => (
              <option key={category.key} value={category.key}>
                {category.label}
              </option>
            ))}
          </select>
        </FieldLabel>
        <FieldLabel label="What the risk is">
          <textarea
            value={description}
            maxLength={RATIONALE_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setDescription(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Owner (the function accountable for it)">
          <input
            value={owner}
            maxLength={TITLE_MAX}
            onChange={(event) => setOwner(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Reason for adding it" hint="Recorded in the audit trail.">
          <input
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
