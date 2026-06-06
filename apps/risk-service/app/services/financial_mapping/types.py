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
    "obligations",
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
    "principal",
    "committed",
    "outstanding",
    "drawn",
    "currency",
    "ccy",
]
type RecordTable = Literal[
    "financial_institutions",
    "financial_accounts",
    "financial_reporting_periods",
    "financial_balances",
    "financial_obligations",
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
OBLIGATION_AMOUNT_ALIASES: tuple[FieldAlias, ...] = (
    "principal",
    "committed",
    "outstanding",
    "drawn",
)
CURRENCY_ALIASES: tuple[FieldAlias, ...] = ("currency", "ccy")
SUPPORTED_FIELD_NAMES = {
    *INSTITUTION_ALIASES,
    *ACCOUNT_ALIASES,
    *PERIOD_ALIASES,
    *BALANCE_AMOUNT_ALIASES,
    *OBLIGATION_AMOUNT_ALIASES,
    *CURRENCY_ALIASES,
}

COUNT_KEYS: tuple[CountKey, ...] = (
    "source_rows",
    "institutions",
    "accounts",
    "reporting_periods",
    "balances",
    "obligations",
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
