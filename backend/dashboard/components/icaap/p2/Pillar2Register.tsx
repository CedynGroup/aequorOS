"use client";

/**
 * The Pillar 2 register: every risk the bank quantifies beyond Pillar 1, with
 * the method that produced the amount and the evidence a reviewer will ask for.
 *
 * What this screen is careful about:
 *
 * DECLARED BASIS, ALWAYS. Every amount carries the basis it is expressed in
 * (B4/D-009). A "% of total RWA" and an amount in the reporting currency look
 * alike in a table and mean entirely different things, so the basis is printed
 * on the row, not inferred.
 *
 * NULL IS "NOT MODELLED", NEVER 0. An item with no stressed amount has not been
 * stressed. Rendering that as zero would assert a measured result.
 *
 * STALENESS IS SHOWN, NOT HIDDEN. When the inputs or the governed parameters
 * moved after an amount was computed, the server says so and the row carries
 * the reasons. An approval on a stale revision is not current, and the screen
 * says that too.
 *
 * REPRESENTATIVE CALIBRATIONS ARE LABELLED (D-039). Several Pillar 2 methods
 * are calibrated on representative band tables pending confirmation. Every item
 * computed from one carries the chip, and so does the register footer.
 */

import { useState } from "react";
import { Calculator, CheckCircle2, Plus, AlertTriangle } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  useApproveIcaapPillar2Item,
  useComputeIcaapPillar2Item,
  useCreateIcaapPillar2Item,
  useIcaapPillar2Register,
  useIcaapTable5,
  type IcaapPillar2Item,
  type IcaapPillar2Register,
} from "@/lib/api/icaapRiskCapital";
import CapitalPlanProposalDialog from "./CapitalPlanProposalDialog";
import ConsistencyControlPanel from "./ConsistencyControlPanel";
import ItemRevisions from "./ItemRevisions";
import ParameterProvenance, {
  PendingCalibrationNote,
} from "./ParameterProvenance";
import Table5Grid from "./Table5Grid";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import {
  DIGEST_CHARS,
  ICON_SM,
  RATIONALE_MAX,
  REASON_MAX,
  ROWS_MEDIUM,
  TITLE_MAX,
} from "./display";
import {
  NOT_MODELLED,
  approvalCopy,
  basisLabel,
  fmtAmount,
  itemStatusCopy,
  methodNote,
  notComputableCopy,
} from "./labels";
import IrrbbSfCard from "./IrrbbSfCard";

export default function Pillar2Register({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canEdit = scope.capitalEdit === true;
  const canApprove = scope.capitalApprove === true;

  const registerQuery = useIcaapPillar2Register(bankId, cycleId);
  const table5Query = useIcaapTable5(bankId, cycleId);
  const [adding, setAdding] = useState(false);
  const [proposing, setProposing] = useState(false);

  const register = registerQuery.data;
  const items = register?.items ?? [];
  const parameters = register?.parameters ?? [];

  const unavailable = p2UnavailableNotice(registerQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Pillar 2" message={unavailable} />;
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
            title="Pillar 2 register"
            subtitle={`Amounts in ${register.currency}.`}
            actions={
              <div className="flex items-center gap-2">
                {canEdit && (
                  <SecondaryButton onClick={() => setAdding(true)}>
                    <Plus size={ICON_SM} aria-hidden />
                    Add an item
                  </SecondaryButton>
                )}
                {canEdit && (
                  <PrimaryButton onClick={() => setProposing(true)}>
                    Propose capital-plan update
                  </PrimaryButton>
                )}
              </div>
            }
          >
            {items.length === 0 ? (
              <EmptyState
                title="No Pillar 2 items yet"
                description="A Pillar 2 item quantifies a risk the Pillar 1 minimum does not fully cover, using a named method whose inputs and parameters are recorded with the amount."
              />
            ) : (
              <div className="space-y-3">
                {items.map((item) => (
                  <ItemRow
                    key={item.itemId}
                    bankId={bankId}
                    cycleId={cycleId}
                    item={item}
                    canEdit={canEdit}
                    canApprove={canApprove}
                  />
                ))}
              </div>
            )}

            {!register.diversificationAllowed && (
              <p className="mt-3 text-caption text-slate">
                A diversification benefit is not permitted for this cycle, so
                the total is the simple sum of the components.
              </p>
            )}

            <PendingCalibrationNote uses={parameters} />
          </SectionCard>

          <IrrbbSfCard register={register} />

          <QueryBoundary
            isLoading={table5Query.isLoading}
            error={table5Query.error}
            onRetry={() => void table5Query.refetch()}
            contained
          >
            {table5Query.data && <Table5Grid table5={table5Query.data} />}
          </QueryBoundary>

          <ConsistencyControlPanel
            title="Source consistency"
            subtitle="The same figures, as the ICAAP states them and as the rest of the platform states them."
            comparisons={register.consistency}
            parameters={parameters}
            canEdit={false}
          />

          {parameters.length > 0 && (
            <SectionCard
              title="Calibration and provenance"
              subtitle="Every parameter these methods read, with its citation and confirmation status."
            >
              <ParameterProvenance uses={parameters} />
            </SectionCard>
          )}
        </div>
      )}

      {adding && register && (
        <AddItemDialog
          bankId={bankId}
          cycleId={cycleId}
          register={register}
          onClose={() => setAdding(false)}
        />
      )}
      {proposing && register && (
        <CapitalPlanProposalDialog
          bankId={bankId}
          cycleId={cycleId}
          register={register}
          onClose={() => setProposing(false)}
        />
      )}
    </QueryBoundary>
  );
}

// ---------------------------------------------------------------------------
// One register item
// ---------------------------------------------------------------------------

function ItemRow({
  bankId,
  cycleId,
  item,
  canEdit,
  canApprove,
}: {
  bankId: string;
  cycleId: string;
  item: IcaapPillar2Item;
  canEdit: boolean;
  canApprove: boolean;
}) {
  const compute = useComputeIcaapPillar2Item(bankId, cycleId);
  const approve = useApproveIcaapPillar2Item(bankId, cycleId);
  const [approving, setApproving] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // The history is fetched only once it is asked for: a register of twenty
  // items would otherwise open twenty reads nobody has looked at.
  const [historyOpen, setHistoryOpen] = useState(false);

  const status = itemStatusCopy(item.methodStatus);

  return (
    <div className="rounded border border-border-light">
      <div className="flex flex-wrap items-start justify-between gap-3 p-3">
        <div className="min-w-0">
          <p className="font-medium text-navy">{item.title}</p>
          <p className="text-caption text-slate">
            {item.methodLabel} · {basisLabel(item.basis)}
          </p>
          {item.statusDetail && (
            <p className="mt-1 flex items-start gap-1 text-caption text-warning">
              <AlertTriangle size={ICON_SM} aria-hidden className="mt-0.5" />
              {notComputableCopy(item.statusDetail)}
            </p>
          )}
          {item.stale && (
            <ul className="mt-1 space-y-0.5 text-caption text-warning">
              {(item.staleReasons ?? []).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex shrink-0 items-start gap-4">
          <div className="text-right">
            <p className="text-caption text-slate">Baseline</p>
            <p className="tnum text-navy">
              {item.baselineAmount === null
                ? NOT_MODELLED
                : fmtAmount(item.baselineAmount)}
            </p>
          </div>
          <div className="text-right">
            <p className="text-caption text-slate">Stressed</p>
            <p className="tnum text-navy">
              {item.stressedAmount === null
                ? NOT_MODELLED
                : fmtAmount(item.stressedAmount)}
            </p>
          </div>
          <div className="text-right">
            <StatusPill tone={status.tone}>{status.label}</StatusPill>
            <p className="mt-1 text-caption text-slate">
              {approvalCopy(item.approvalCurrent).label}
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-light bg-surface/60 px-3 py-2">
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={() => setExpanded((open) => !open)}
            className="text-caption text-slate underline-offset-2 hover:underline"
          >
            {expanded ? "Hide the calculation" : "Show the calculation"}
          </button>
          <button
            type="button"
            onClick={() => setHistoryOpen((open) => !open)}
            className="text-caption text-slate underline-offset-2 hover:underline"
          >
            {historyOpen ? "Hide the history" : "Show the history"}
          </button>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && item.editable && (
            <SecondaryButton
              disabled={compute.isPending}
              onClick={() =>
                compute.mutate({
                  itemId: item.itemId,
                  payload: {
                    baseRevisionNo: item.revisionNo,
                    reason: "Recompute from the current inputs",
                  },
                })
              }
            >
              <Calculator size={ICON_SM} aria-hidden />
              {compute.isPending ? "Computing…" : "Compute"}
            </SecondaryButton>
          )}
          {canApprove && item.approvable && (
            <PrimaryButton onClick={() => setApproving(true)}>
              <CheckCircle2 size={ICON_SM} aria-hidden />
              Approve
            </PrimaryButton>
          )}
        </div>
      </div>

      {(compute.isError || approve.isError) && (
        <p className="border-t border-border-light bg-critical-light/40 px-3 py-2 text-caption text-navy/80">
          {((compute.error ?? approve.error) as Error).message}
        </p>
      )}

      {expanded && (
        <div className="space-y-3 border-t border-border-light p-3">
          {item.scenarioDefinition && (
            <div>
              <p className="text-caption text-slate">Scenario</p>
              <p className="text-body text-navy/80">
                {item.scenarioDefinition}
              </p>
            </div>
          )}
          {item.rationale && (
            <div>
              <p className="text-caption text-slate">Rationale</p>
              <p className="text-body text-navy/80">{item.rationale}</p>
            </div>
          )}
          {item.computation && (
            <>
              <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                {item.computation.map((entry) => (
                  <div key={entry.label} className="flex justify-between gap-3">
                    <dt className="text-caption text-slate">{entry.label}</dt>
                    <dd className="tnum text-caption text-navy">
                      {entry.value ?? NOT_MODELLED}
                    </dd>
                  </div>
                ))}
              </dl>
              {item.pendingParameters.length > 0 && (
                <p className="text-caption text-warning">
                  Awaiting confirmation of: {item.pendingParameters.join(", ")}
                </p>
              )}
              <ParameterProvenance uses={item.parameters} />
            </>
          )}
          {item.inputsDigest && (
            <p className="text-caption text-slate">
              Inputs digest {item.inputsDigest.slice(0, DIGEST_CHARS)}… ·
              revision {item.revisionNo}
            </p>
          )}
        </div>
      )}

      {historyOpen && (
        <div className="border-t border-border-light p-3">
          <ItemRevisions
            bankId={bankId}
            cycleId={cycleId}
            itemId={item.itemId}
            approvedRevisionNo={item.approvedRevisionNo}
          />
        </div>
      )}

      {approving && (
        <ApproveDialog
          item={item}
          isSaving={approve.isPending}
          onClose={() => setApproving(false)}
          onSubmit={(note) => {
            approve.mutate(
              {
                itemId: item.itemId,
                payload: { revisionNo: item.revisionNo, note },
              },
              { onSuccess: () => setApproving(false) },
            );
          }}
        />
      )}
    </div>
  );
}

function ApproveDialog({
  item,
  isSaving,
  onClose,
  onSubmit,
}: {
  item: IcaapPillar2Item;
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (note: string) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <Dialog
      title={`Approve ${item.title}`}
      description={`Approving revision ${item.revisionNo}. You cannot approve an item you authored, and a later recomputation makes this approval no longer current.`}
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={isSaving || note.trim() === ""}
            onClick={() => onSubmit(note.trim())}
          >
            {isSaving ? "Approving…" : "Approve"}
          </PrimaryButton>
        </>
      }
    >
      <FieldLabel
        label="Approval note"
        hint="What you checked. Recorded against the revision, permanently."
      >
        <textarea
          value={note}
          maxLength={RATIONALE_MAX}
          rows={ROWS_MEDIUM}
          onChange={(event) => setNote(event.target.value)}
          className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
        />
      </FieldLabel>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Add an item
// ---------------------------------------------------------------------------

function AddItemDialog({
  bankId,
  cycleId,
  register,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  register: IcaapPillar2Register;
  onClose: () => void;
}) {
  const mutation = useCreateIcaapPillar2Item(bankId, cycleId);
  const [title, setTitle] = useState("");
  const [categoryKey, setCategoryKey] = useState("");
  const [method, setMethod] = useState("");
  const [scenario, setScenario] = useState("");
  const [rationale, setRationale] = useState("");
  const [reason, setReason] = useState("");

  /**
   * The categories and methods offered are the ones the server already used for
   * this cycle's components. There is no hardcoded method list here: the
   * catalogue is framework and licence data, and the API refuses a method it
   * does not allow with its own message.
   */
  const known = register.items ?? [];
  const categories = [...new Set(known.map((item) => item.categoryKey))];
  /**
   * The method options carry the SERVER'S OWN TITLE as the option label.
   *
   * The datalist must submit the wire key — that is what the API accepts — but
   * a bare `<option value="granularity_adjustment">` printed that key straight
   * at a preparer, which is the raw-enum defect #203 exists to prevent. The
   * label comes off the register's own rows, so it can never disagree with the
   * title the register prints beside the amount.
   */
  const methods = [
    ...new Map(
      known.map((item) => [item.method, item.methodLabel] as const),
    ).entries(),
  ];

  return (
    <Dialog
      title="Add a Pillar 2 item"
      description="The method decides what inputs are read and what evidence is required. The server refuses a method this cycle's basis cannot support."
      onClose={onClose}
      wide
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              title.trim() === "" ||
              categoryKey.trim() === "" ||
              method.trim() === "" ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                {
                  componentKey: categoryKey.trim(),
                  method: method.trim(),
                  // The wire takes a structured scenario body, not prose. The
                  // operator's sentence is recorded under a named key rather
                  // than this form inventing a schema for it.
                  scenarioDefinition:
                    scenario.trim() === ""
                      ? null
                      : { description: scenario.trim() },
                  rationale:
                    rationale.trim() === "" ? null : rationale.trim(),
                  reason: reason.trim(),
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Adding…" : "Add item"}
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
        <FieldLabel label="Title">
          <input
            value={title}
            maxLength={TITLE_MAX}
            onChange={(event) => setTitle(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <div className="grid gap-3 sm:grid-cols-2">
          <FieldLabel label="Risk category">
            <input
              list="icaap-p2-categories"
              value={categoryKey}
              onChange={(event) => setCategoryKey(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
            <datalist id="icaap-p2-categories">
              {categories.map((key) => (
                <option key={key} value={key} />
              ))}
            </datalist>
          </FieldLabel>
          <FieldLabel label="Method">
            <input
              list="icaap-p2-methods"
              value={method}
              onChange={(event) => setMethod(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
            <datalist id="icaap-p2-methods">
              {methods.map(([key, label]) => (
                <option key={key} value={key} label={label} />
              ))}
            </datalist>
            {methodNote(method) === null ? null : (
              <p className="mt-1 text-caption leading-relaxed text-slate">
                {methodNote(method)}
              </p>
            )}
          </FieldLabel>
        </div>
        <FieldLabel
          label="Scenario definition"
          hint="Required for a scenario-based method. Say what is assumed to happen."
        >
          <textarea
            value={scenario}
            maxLength={RATIONALE_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setScenario(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
        <FieldLabel label="Rationale">
          <textarea
            value={rationale}
            maxLength={RATIONALE_MAX}
            rows={ROWS_MEDIUM}
            onChange={(event) => setRationale(event.target.value)}
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
