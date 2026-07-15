from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.models import Document, DocumentExtraction
from app.schemas.common import JsonObject, JsonValue

MAPPER_VERSION = "financial_workspace_mapper_v1"

type CountKey = Literal[
    "source_rows",
    "institutions",
    "accounts",
    "reporting_periods",
    "balances",
    "cash_flows",
    "obligations",
    "covenants",
    "record_source_links",
]
type MapperCounts = dict[str, int]
type FieldAlias = Literal[
    "institution",
    "bank",
    "lender",
    "counterparty",
    "account",
    "account_name",
    "account_type",
    "period",
    "period_start",
    "period_end",
    "as_of_date",
    "amount",
    "balance",
    "cash_flow",
    "cashflow",
    "cash_flow_amount",
    "net_cash_flow",
    "inflow",
    "outflow",
    "direction",
    "cash_flow_direction",
    "flow_direction",
    "category",
    "cash_flow_category",
    "transaction_category",
    "cash_flow_date",
    "flow_date",
    "date",
    "principal",
    "committed",
    "outstanding",
    "drawn",
    "currency",
    "ccy",
    "covenant_name",
    "covenant_metric",
    "covenant_operator",
    "covenant_threshold",
    "covenant_actual_value",
    "covenant_compliance_status",
]
type RecordTable = Literal[
    "financial_institutions",
    "financial_accounts",
    "financial_reporting_periods",
    "financial_balances",
    "financial_cash_flows",
    "financial_obligations",
    "financial_covenants",
]

INSTITUTION_ALIASES: tuple[FieldAlias, ...] = (
    "institution",
    "bank",
    "lender",
    "counterparty",
)
ACCOUNT_ALIASES: tuple[FieldAlias, ...] = ("account", "account_name", "account_type")
PERIOD_ALIASES: tuple[FieldAlias, ...] = (
    "period",
    "period_start",
    "period_end",
    "as_of_date",
)
BALANCE_AMOUNT_ALIASES: tuple[FieldAlias, ...] = ("amount", "balance")
CASH_FLOW_AMOUNT_ALIASES: tuple[FieldAlias, ...] = (
    "cash_flow",
    "cashflow",
    "cash_flow_amount",
    "net_cash_flow",
    "inflow",
    "outflow",
    "amount",
)
CASH_FLOW_DIRECTION_ALIASES: tuple[FieldAlias, ...] = (
    "direction",
    "cash_flow_direction",
    "flow_direction",
)
CASH_FLOW_CATEGORY_ALIASES: tuple[FieldAlias, ...] = (
    "category",
    "cash_flow_category",
    "transaction_category",
)
CASH_FLOW_DATE_ALIASES: tuple[FieldAlias, ...] = (
    "cash_flow_date",
    "flow_date",
    "date",
)
OBLIGATION_AMOUNT_ALIASES: tuple[FieldAlias, ...] = (
    "principal",
    "committed",
    "outstanding",
    "drawn",
)
CURRENCY_ALIASES: tuple[FieldAlias, ...] = ("currency", "ccy")
COVENANT_NAME_ALIASES: tuple[FieldAlias, ...] = ("covenant_name",)
COVENANT_METRIC_ALIASES: tuple[FieldAlias, ...] = ("covenant_metric",)
COVENANT_OPERATOR_ALIASES: tuple[FieldAlias, ...] = ("covenant_operator",)
COVENANT_THRESHOLD_ALIASES: tuple[FieldAlias, ...] = ("covenant_threshold",)
COVENANT_ACTUAL_ALIASES: tuple[FieldAlias, ...] = ("covenant_actual_value",)
COVENANT_STATUS_ALIASES: tuple[FieldAlias, ...] = ("covenant_compliance_status",)
SUPPORTED_FIELD_NAMES = {
    *INSTITUTION_ALIASES,
    *ACCOUNT_ALIASES,
    *PERIOD_ALIASES,
    *BALANCE_AMOUNT_ALIASES,
    *CASH_FLOW_AMOUNT_ALIASES,
    *CASH_FLOW_DIRECTION_ALIASES,
    *CASH_FLOW_CATEGORY_ALIASES,
    *CASH_FLOW_DATE_ALIASES,
    *OBLIGATION_AMOUNT_ALIASES,
    *CURRENCY_ALIASES,
    *COVENANT_NAME_ALIASES,
    *COVENANT_METRIC_ALIASES,
    *COVENANT_OPERATOR_ALIASES,
    *COVENANT_THRESHOLD_ALIASES,
    *COVENANT_ACTUAL_ALIASES,
    *COVENANT_STATUS_ALIASES,
}

COUNT_KEYS: tuple[CountKey, ...] = (
    "source_rows",
    "institutions",
    "accounts",
    "reporting_periods",
    "balances",
    "cash_flows",
    "obligations",
    "covenants",
    "record_source_links",
)


@dataclass(frozen=True)
class ExtractedRow:
    index: int
    payload: JsonObject
    locator: JsonObject


@dataclass(frozen=True)
class FieldValue:
    canonical_name: FieldAlias | str
    source_field: str
    value: JsonValue


@dataclass(frozen=True)
class DecimalField:
    source_field: str
    value: Decimal


@dataclass(frozen=True)
class ResolvedExtraction:
    document: Document
    extraction: DocumentExtraction


def empty_counts() -> MapperCounts:
    return {key: 0 for key in COUNT_KEYS}


def count(counts: MapperCounts, key: CountKey) -> None:
    counts[key] = counts.get(key, 0) + 1
