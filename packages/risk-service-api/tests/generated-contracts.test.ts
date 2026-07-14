import { AccountId, instanceOfAccountId } from "../src/models/AccountId";
import { AsOfDate, instanceOfAsOfDate } from "../src/models/AsOfDate";
import {
  CalculationRerunCreateToJSON,
  type CalculationRerunCreate,
} from "../src/models/CalculationRerunCreate";
import {
  CalculationRunCreateToJSON,
  type CalculationRunCreate,
} from "../src/models/CalculationRunCreate";
import {
  CalculationRunReadFromJSON,
  CalculationRunReadToJSON,
  type CalculationRunRead,
} from "../src/models/CalculationRunRead";
import type { CalculationRunSummaryRead } from "../src/models/CalculationRunSummaryRead";
import {
  CapitalComparisonReadFromJSON,
  CapitalComparisonReadToJSON,
} from "../src/models/CapitalComparisonRead";
import {
  CapitalProjectionCreateToJSON,
  type CapitalProjectionCreate,
} from "../src/models/CapitalProjectionCreate";
import {
  CapitalProjectionReadFromJSON,
  CapitalProjectionReadToJSON,
} from "../src/models/CapitalProjectionRead";
import {
  CapitalProjectionSummaryReadFromJSON,
  CapitalProjectionSummaryReadToJSON,
} from "../src/models/CapitalProjectionSummaryRead";
import type { FinancialAccountUpdate } from "../src/models/FinancialAccountUpdate";
import {
  FinancialAmount,
  instanceOfFinancialAmount,
} from "../src/models/FinancialAmount";
import {
  FinancialBalanceCreate,
  FinancialBalanceCreateToJSON,
} from "../src/models/FinancialBalanceCreate";
import type { AssumptionValue } from "../src/models/AssumptionValue";
import {
  AssumptionUpdate,
  AssumptionUpdateToJSON,
} from "../src/models/AssumptionUpdate";
import type { ScenarioRead } from "../src/models/ScenarioRead";
import type { ScenarioAssumptionRead } from "../src/models/ScenarioAssumptionRead";

type Equal<Left, Right> =
  (<Value>() => Value extends Left ? 1 : 2) extends <
    Value,
  >() => Value extends Right ? 1 : 2
    ? true
    : false;
type Assert<Value extends true> = Value;

type AmountContract = Assert<Equal<FinancialAmount, number | string>>;
type AccountIdContract = Assert<Equal<AccountId, string | null>>;
type DateContract = Assert<Equal<AsOfDate, string | null>>;
type CalculationCreateDateContract = Assert<
  Equal<CalculationRunCreate["asOfDate"], Date | null | undefined>
>;
type CalculationRerunDateContract = Assert<
  Equal<CalculationRerunCreate["asOfDate"], Date | null | undefined>
>;
type CalculationStartedAtContract = Assert<
  Equal<CalculationRunRead["startedAt"], Date | null>
>;
type CalculationCompletedAtContract = Assert<
  Equal<CalculationRunSummaryRead["completedAt"], Date | null>
>;
type MetadataContract = Assert<
  Equal<
    FinancialAccountUpdate["metadata"],
    { [key: string]: any } | null | undefined
  >
>;
type AssumptionValueContract = Assert<
  Equal<AssumptionValue, string | number | boolean | null>
>;
type ScenarioAssumptionsContract = Assert<
  Equal<ScenarioRead["assumptions"], Array<ScenarioAssumptionRead>>
>;

const closedPayload: FinancialBalanceCreate = {
  accountId: "0198c7de-95bf-7000-8000-000000000001",
  amount: "125.50",
  balanceType: "cash",
  reason: "manual",
  // @ts-expect-error closed mutation models reject unknown properties
  unexpected: "must be rejected",
};

const payload: FinancialBalanceCreate & { unexpected: string } = {
  accountId: "0198c7de-95bf-7000-8000-000000000001",
  amount: "125.50",
  balanceType: "cash",
  reason: "manual",
  unexpected: "must not be serialized",
};
const serialized = FinancialBalanceCreateToJSON(payload) as unknown as Record<
  string,
  unknown
>;
const assumptionSerialized = AssumptionUpdateToJSON({
  value: 0.05,
  reason: "Reviewer update",
} satisfies AssumptionUpdate) as unknown as Record<string, unknown>;
const calculationDate = new Date("2026-07-12T14:30:00.000Z");
const calculationCreateSerialized = CalculationRunCreateToJSON({
  scenarioId: "0198c7de-95bf-7000-8000-000000000001",
  asOfDate: calculationDate,
}) as unknown as Record<string, unknown>;
const calculationRerunSerialized = CalculationRerunCreateToJSON({
  asOfDate: calculationDate,
}) as unknown as Record<string, unknown>;
const calculationRead = CalculationRunReadFromJSON({
  as_of_date: "2026-07-12",
  case_id: "0198c7de-95bf-7000-8000-000000000002",
  completed_at: "2026-07-12T15:30:00.000Z",
  created_at: "2026-07-12T14:00:00.000Z",
  created_by: "0198c7de-95bf-7000-8000-000000000003",
  engine_version: "balance-sheet-v1.0.0",
  error: null,
  forecast_periods: 3,
  id: "0198c7de-95bf-7000-8000-000000000004",
  input_hash: "a".repeat(64),
  input_schema_version: "calculation-input-v1",
  inputs: {},
  organization_id: "0198c7de-95bf-7000-8000-000000000005",
  output_schema_version: "balance-sheet-output-v1",
  outputs: [],
  rerun_of_run_id: null,
  scenario_id: "0198c7de-95bf-7000-8000-000000000001",
  started_at: "2026-07-12T14:30:00.000Z",
  status: "succeeded",
  updated_at: "2026-07-12T15:30:00.000Z",
});
const calculationReadSerialized = CalculationRunReadToJSON(
  calculationRead,
) as unknown as Record<string, unknown>;
const calculationReadWithoutTiming = CalculationRunReadFromJSON({
  ...calculationReadSerialized,
  completed_at: null,
  started_at: null,
});
const capitalCreateSerialized = CapitalProjectionCreateToJSON({
  calculationRunId: "0198c7de-95bf-7000-8000-000000000004",
} satisfies CapitalProjectionCreate) as unknown as Record<string, unknown>;
const capitalRead = CapitalProjectionReadFromJSON({
  id: "0198c7de-95bf-7000-8000-000000000010",
  organization_id: "0198c7de-95bf-7000-8000-000000000005",
  case_id: "0198c7de-95bf-7000-8000-000000000002",
  scenario_id: "0198c7de-95bf-7000-8000-000000000001",
  calculation_run_id: "0198c7de-95bf-7000-8000-000000000004",
  status: "succeeded",
  engine_version: "capital-projection-v1.0.0",
  input_hash: "a".repeat(64),
  reporting_currency: "USD",
  started_at: "2026-07-12T14:30:00.000Z",
  completed_at: "2026-07-12T15:30:00.000Z",
  error: null,
  indicators: [
    {
      id: "0198c7de-95bf-7000-8000-000000000011",
      forecast_period_id: "0198c7de-95bf-7000-8000-000000000012",
      period_number: 1,
      equity: "125.0000",
      equity_to_assets_ratio: "0.12500000",
      liabilities_to_assets_ratio: "0.87500000",
      equity_change: "-25.0000",
      pressure_level: "medium",
      evidence: { calculation_run_id: "0198c7de-95bf-7000-8000-000000000004" },
    },
  ],
  findings: [],
  created_by: "0198c7de-95bf-7000-8000-000000000003",
  created_at: "2026-07-12T14:30:00.000Z",
  updated_at: "2026-07-12T15:30:00.000Z",
});
const capitalReadSerialized = CapitalProjectionReadToJSON(
  capitalRead,
) as unknown as Record<string, unknown>;
const capitalSummaryRead = CapitalProjectionSummaryReadFromJSON({
  id: "0198c7de-95bf-7000-8000-000000000010",
  scenario_id: "0198c7de-95bf-7000-8000-000000000001",
  calculation_run_id: "0198c7de-95bf-7000-8000-000000000004",
  status: "failed",
  reporting_currency: "USD",
  started_at: "2026-07-12T14:30:00.000Z",
  completed_at: "2026-07-12T15:30:00.000Z",
  created_at: "2026-07-12T14:30:00.000Z",
});
const capitalSummarySerialized = CapitalProjectionSummaryReadToJSON(
  capitalSummaryRead,
) as unknown as Record<string, unknown>;
const capitalComparison = CapitalComparisonReadFromJSON({
  case_id: "0198c7de-95bf-7000-8000-000000000002",
  baseline: null,
  downside: null,
  periods: [],
  diagnostic: {
    code: "comparison_basis_mismatch",
    message: "Forecast bases differ.",
    differing_attributes: [
      "as_of_date",
      "reporting_currency",
      "forecast_horizon",
    ],
    baseline_basis: {
      as_of_date: "2026-06-30",
      reporting_currency: "USD",
      forecast_horizon: 2,
    },
    downside_basis: {
      as_of_date: "2026-07-01",
      reporting_currency: "EUR",
      forecast_horizon: 3,
    },
    corrective_action: "Rerun the other scenario.",
  },
});
const capitalComparisonSerialized = CapitalComparisonReadToJSON(
  capitalComparison,
) as unknown as Record<string, any>;

assert(
  serialized.account_id === payload.accountId,
  "account_id was not serialized",
);
assert(
  serialized.balance_type === payload.balanceType,
  "balance_type was not serialized",
);
assert(!("accountId" in serialized), "camelCase accountId leaked into JSON");
assert(
  !("balanceType" in serialized),
  "camelCase balanceType leaked into JSON",
);
assert(!("unexpected" in serialized), "unknown property leaked into JSON");
assert(
  assumptionSerialized.value === 0.05,
  "assumption value was not serialized",
);
assert(
  assumptionSerialized.reason === "Reviewer update",
  "assumption reason was not serialized",
);
assert(instanceOfFinancialAmount(125.5), "number amount was rejected");
assert(instanceOfFinancialAmount("125.5"), "string amount was rejected");
assert(!instanceOfFinancialAmount({}), "object was accepted as an amount");
assert(instanceOfAccountId(null), "nullable account ID rejected null");
assert(
  instanceOfAccountId("0198c7de-95bf-7000-8000-000000000001"),
  "account ID rejected string",
);
assert(!instanceOfAccountId(42), "number was accepted as an account ID");
assert(instanceOfAsOfDate("2026-07-12"), "date alias rejected string");
assert(!instanceOfAsOfDate({}), "object was accepted as a date alias");
assert(
  calculationCreateSerialized.as_of_date === "2026-07-12",
  "calculation as-of date was not serialized",
);
assert(
  calculationRerunSerialized.as_of_date === "2026-07-12",
  "calculation rerun as-of date was not serialized",
);
assert(
  calculationRead.startedAt instanceof Date,
  "calculation start time was not decoded as a Date",
);
assert(
  calculationRead.completedAt instanceof Date,
  "calculation completion time was not decoded as a Date",
);
assert(
  calculationReadSerialized.started_at === "2026-07-12T14:30:00.000Z",
  "calculation start time was not serialized",
);
assert(
  calculationReadWithoutTiming.startedAt === null &&
    calculationReadWithoutTiming.completedAt === null,
  "nullable calculation timestamps were not preserved",
);
assert(
  capitalCreateSerialized.calculation_run_id ===
    "0198c7de-95bf-7000-8000-000000000004",
  "capital calculation run ID was not serialized",
);
assert(
  capitalRead.indicators[0]?.equityToAssetsRatio === "0.12500000",
  "capital indicator ratio was not decoded",
);
assert(
  capitalRead.indicators[0]?.evidence.calculation_run_id ===
    capitalRead.calculationRunId,
  "capital evidence traceability was not decoded",
);
assert(
  capitalReadSerialized.calculation_run_id === capitalRead.calculationRunId,
  "capital projection was not serialized with snake-case run ID",
);
assert(
  capitalReadSerialized.reporting_currency === "USD",
  "capital projection reporting currency was not serialized",
);
assert(
  capitalSummaryRead.completedAt === "2026-07-12T15:30:00.000Z",
  "capital projection summary completion time was not decoded",
);
assert(
  capitalSummarySerialized.scenario_id === capitalSummaryRead.scenarioId,
  "capital projection summary scenario ID was not serialized",
);
assert(
  capitalSummarySerialized.reporting_currency === "USD",
  "capital projection summary reporting currency was not serialized",
);
assert(
  capitalComparison.diagnostic.baselineBasis.asOfDate instanceof Date,
  "capital comparison basis date was not decoded",
);
assert(
  capitalComparison.diagnostic.differingAttributes.join(",") ===
    "as_of_date,reporting_currency,forecast_horizon",
  "capital comparison differing attributes were not decoded",
);
assert(
  capitalComparisonSerialized.diagnostic.downside_basis.reporting_currency ===
    "EUR",
  "capital comparison diagnostic was not serialized with snake-case basis fields",
);

function assert(condition: boolean, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

void (0 as unknown as AmountContract);
void (0 as unknown as AccountIdContract);
void (0 as unknown as DateContract);
void (0 as unknown as CalculationCreateDateContract);
void (0 as unknown as CalculationRerunDateContract);
void (0 as unknown as CalculationStartedAtContract);
void (0 as unknown as CalculationCompletedAtContract);
void (0 as unknown as MetadataContract);
void (0 as unknown as AssumptionValueContract);
void (0 as unknown as ScenarioAssumptionsContract);
void closedPayload;
