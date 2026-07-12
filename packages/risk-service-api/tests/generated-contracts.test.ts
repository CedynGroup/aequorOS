import { AccountId, instanceOfAccountId } from "../src/models/AccountId";
import { AsOfDate, instanceOfAsOfDate } from "../src/models/AsOfDate";
import type { FinancialAccountUpdateMetadata } from "../src/models/FinancialAccountUpdateMetadata";
import {
  FinancialAmount,
  instanceOfFinancialAmount,
} from "../src/models/FinancialAmount";
import {
  FinancialBalanceCreate,
  FinancialBalanceCreateToJSON,
} from "../src/models/FinancialBalanceCreate";

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
type MetadataContract = Assert<
  Equal<FinancialAccountUpdateMetadata, { [key: string]: any } | null>
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

function assert(condition: boolean, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

void (0 as unknown as AmountContract);
void (0 as unknown as AccountIdContract);
void (0 as unknown as DateContract);
void (0 as unknown as MetadataContract);
void closedPayload;
