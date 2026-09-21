"use client";

/**
 * The likelihood x impact matrix, drawn from the server's own grid.
 *
 * D-024 in its sharpest form. Every cell's score, its rating band and whether a
 * risk landing there is MATERIAL are decided by the API
 * (`IcaapMaterialityMatrix.cells`), from the governed score bands and the
 * governed materiality thresholds. This component draws what it is given:
 *
 *   - the axes come from `likelihoodLevels` / `impactLevels`, so a framework
 *     with a 4x4 or 6x6 matrix renders correctly without a code change;
 *   - a cell's colour is keyed on its BAND KEY, never on its score, so moving
 *     a band edge in the console recolours the matrix with no edit here;
 *   - the "material at ..." caption is printed from `materialMinScore` /
 *     `materialMinImpact` and carries the provenance of the parameters those
 *     came from (D-039).
 *
 * There is no local band table, no local threshold, and no arithmetic on a
 * score anywhere in this file.
 */

import SectionCard from "@/components/ui/SectionCard";
import type {
  IcaapMaterialityMatrix,
  IcaapRisk,
} from "@/lib/api/icaapRiskCapital";
import { MATRIX_CELL_MIN_PX } from "./display";
import { NOT_ASSESSED } from "./labels";
import ParameterProvenance from "./ParameterProvenance";

/**
 * Band key -> heat. The KEYS are framework data; this is only the palette they
 * are drawn in, and an unknown key degrades to neutral rather than guessing.
 */
const BAND_TONE: Record<string, string> = {
  low: "bg-success-light text-success border-success/30",
  minor: "bg-success-light text-success border-success/30",
  moderate: "bg-warning-light/60 text-warning border-warning/30",
  medium: "bg-warning-light/60 text-warning border-warning/30",
  elevated: "bg-warning-light text-warning border-warning/40",
  high: "bg-critical-light/60 text-critical border-critical/30",
  severe: "bg-critical-light text-critical border-critical/40",
  critical: "bg-critical-light text-critical border-critical/50",
  extreme: "bg-critical-light text-critical border-critical/50",
};

function bandTone(ratingKey: string): string {
  return BAND_TONE[ratingKey] ?? "bg-surface text-slate border-border";
}

export default function MaterialityMatrix({
  matrix,
  risks,
}: {
  /**
   * Nullable on purpose. The normaliser guarantees these for data that came
   * through a hook, but a component that throws when a prop is absent is a
   * component that can only be used one way.
   */
  matrix: IcaapMaterialityMatrix | null | undefined;
  risks: readonly IcaapRisk[] | null | undefined;
}) {
  const likelihoodLevels = matrix?.likelihoodLevels ?? [];
  const impactLevels = matrix?.impactLevels ?? [];
  const cells = matrix?.cells ?? [];
  const placed = risks ?? [];

  /** The scored risks that sit in each cell, so the reader sees the spread. */
  const occupants = new Map<string, IcaapRisk[]>();
  for (const risk of placed) {
    if (risk.likelihoodScore === null || risk.impactScore === null) continue;
    const key = `${risk.likelihoodScore}:${risk.impactScore}`;
    const bucket = occupants.get(key);
    if (bucket) bucket.push(risk);
    else occupants.set(key, [risk]);
  }

  const cellAt = (likelihood: number, impact: number) =>
    cells.find(
      (cell) => cell.likelihood === likelihood && cell.impact === impact,
    );

  const thresholdCaption = captionFor(matrix);
  const unplaced = placed.filter(
    (risk) => risk.likelihoodScore === null || risk.impactScore === null,
  );

  return (
    <SectionCard
      title="Materiality matrix"
      subtitle={thresholdCaption}
      footer={<ParameterProvenance uses={matrix?.parameters} compact />}
    >
      {likelihoodLevels.length === 0 || impactLevels.length === 0 ? (
        <p className="text-body text-slate">
          The framework for this cycle does not define a materiality matrix.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-separate border-spacing-1">
            <caption className="sr-only">
              Likelihood by impact, with each cell&apos;s inherent rating
            </caption>
            <thead>
              <tr>
                <th
                  scope="col"
                  className="text-left text-caption font-medium text-slate"
                >
                  Likelihood \ Impact
                </th>
                {impactLevels.map((level) => (
                  <th
                    key={level.score}
                    scope="col"
                    className="text-caption font-medium text-slate"
                  >
                    {level.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...likelihoodLevels].reverse().map((likelihood) => (
                <tr key={likelihood.score}>
                  <th
                    scope="row"
                    className="whitespace-nowrap pr-2 text-left text-caption font-medium text-slate"
                  >
                    {likelihood.label}
                  </th>
                  {impactLevels.map((impact) => {
                    const cell = cellAt(likelihood.score, impact.score);
                    const here =
                      occupants.get(`${likelihood.score}:${impact.score}`) ?? [];
                    if (!cell) {
                      return (
                        <td
                          key={impact.score}
                          className="rounded border border-dashed border-border bg-surface/50 p-2 text-center text-caption text-slate"
                          style={{ minWidth: MATRIX_CELL_MIN_PX }}
                        >
                          {NOT_ASSESSED}
                        </td>
                      );
                    }
                    return (
                      <td
                        key={impact.score}
                        style={{ minWidth: MATRIX_CELL_MIN_PX }}
                        className={`rounded border p-2 align-top ${bandTone(cell.ratingKey)} ${
                          cell.material ? "ring-2 ring-navy/40" : ""
                        }`}
                        title={
                          cell.material
                            ? `${cell.ratingLabel} — a risk here is material`
                            : `${cell.ratingLabel} — a risk here is not material on the matrix`
                        }
                      >
                        <span className="block text-caption font-medium">
                          {cell.ratingLabel}
                        </span>
                        {here.length > 0 && (
                          <ul className="mt-1 space-y-0.5 text-caption font-normal text-navy">
                            {here.map((risk) => (
                              <li key={risk.riskKey} className="truncate">
                                {risk.title}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-3 text-caption text-slate">
        A cell with a heavier outline is one the framework treats as material.
      </p>

      {unplaced.length > 0 && (
        <p className="mt-1 text-caption text-slate">
          {unplaced.length} risk{unplaced.length === 1 ? " is" : "s are"} not on
          the matrix because {unplaced.length === 1 ? "it has" : "they have"} no
          likelihood and impact score yet.
        </p>
      )}
    </SectionCard>
  );
}

/**
 * The threshold sentence, built ONLY from what the payload states.
 *
 * When the server sends no threshold, the caption says the matrix is not the
 * deciding rule rather than inventing one.
 */
function captionFor(
  matrix: IcaapMaterialityMatrix | null | undefined,
): string {
  const parts: string[] = [];
  if (matrix?.materialMinScore !== null && matrix?.materialMinScore !== undefined) {
    parts.push(`a score of ${matrix.materialMinScore} or more`);
  }
  if (matrix?.materialMinImpact !== null && matrix?.materialMinImpact !== undefined) {
    parts.push(`an impact of ${matrix.materialMinImpact} or more`);
  }
  if (parts.length === 0) {
    return "The governed materiality thresholds are not available, so the matrix shows ratings only.";
  }
  return `A risk is material at ${parts.join(", or ")}.`;
}
