"""The bank's exposure book, in the shape the granularity adjustment consumes.

The pure derivation in ``app/domain/credit/granularity.py`` takes a list of
obligor exposures, each already carrying a PD, an ELGD and a correlation
segment. Nothing in a bank's canonical book arrives in that shape. This module
is the translation, and it is the only place a figure the adjustment uses is
CHOSEN rather than calculated — so every choice it makes is named, ordered and
disclosed, and the ones it cannot make are refusals.

**Precedence (P5-DESIGN §4.2), in order, never a nearest match.**

* **PD:** the exposure's own ``attributes.pd_pct``, then the Board-approved ECL
  register's stage-1 (12-month) PD for segment ``ALL``, then the governed proxy
  table keyed by the exposure's risk-weight code. Stage-2 LIFETIME PDs are never
  used — a lifetime PD in a one-year capital figure overstates it. An exposure
  that reaches the end of that list is ``ga_pd_unresolved``, naming a sample of
  the references, and the whole method declines: a book where some obligors have
  a real PD and the rest have a plug is not a granularity measurement.
* **ELGD:** the exposure's own ``attributes.lgd_pct``, then the ECL register's
  stage-1 LGD, then the governed default.
* **Segment:** the governed counterparty-type map. A type the map does not carry
  is ``ga_segment_unmapped``, never a guessed correlation.

**What is excluded, with the count disclosed on the result:** IFRS 9 stage 3
(already defaulted, and covered by the ECL rather than by unexpected loss), the
sovereign component (zero-risk-weighted rows and the types the governed map
sends to ``excluded``), rows whose amount was never converted into the reporting
currency, and rows with no positive exposure. Every one of those is a decision
an operator can see and argue with; none of them silently shrinks the book.

**Maturity.** The maturity adjustment is a governed switch, seeded off. When it
is on, this module reads an effective maturity in YEARS that the source stated
(``attributes.maturity_years`` or a numeric ``attributes.maturity``); it does
NOT turn a contractual maturity date into one. The IRB effective maturity is a
cash-flow-weighted figure and the year fraction is a day-count convention —
neither is governed here, and inventing either would put an unapproved number
inside a capital charge (D-024). An exposure with the switch on and no stated
effective maturity is ``ga_maturity_unresolved``.

Every governed value arrives through the control plane; an absent row is
:class:`~app.domain.icaap.pillar2.types.MissingParameter` naming its code, which
the service turns into ``missing_parameter``. The proxy table and the default
ELGD are required only when a row actually falls to them, so a bank whose book
carries its own estimates is not blocked on a row it never reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.capital.engine import ZERO_RISK_WEIGHT_CODE
from app.domain.credit.granularity import (
    SEGMENT_CORPORATE,
    SEGMENT_EXCLUDED,
    SEGMENT_RETAIL_OTHER,
    GaExposure,
    Segment,
)
from app.domain.icaap.pillar2.granularity_method import (
    KEY_SCHEMA,
    PARAM_COUNTERPARTY_SEGMENT_MAP,
    PARAM_DEFAULT_ELGD_PCT,
    PARAM_MATURITY_ADJUSTMENT,
    PARAM_PROXY_PD_BY_RW_CODE,
    parse_maturity_adjustment,
)
from app.domain.icaap.pillar2.types import MissingParameter, ParameterUse
from app.domain.icaap.units import HUNDRED
from app.models import Bank, ParamEclAssumption
from app.models.icaap import IcaapCycle
from app.services import credit_exposure_book
from app.services.icaap import params
from app.services.params import get_active_params

#: The governed rows this builder reads, on top of the six the formula reads.
BOOK_PARAMETER_CODES: tuple[str, ...] = (
    PARAM_COUNTERPARTY_SEGMENT_MAP,
    PARAM_DEFAULT_ELGD_PCT,
    PARAM_PROXY_PD_BY_RW_CODE,
)
#: Plus the maturity switch, because whether a maturity is REQUIRED is governed.
_RESOLVED_CODES: tuple[str, ...] = (*BOOK_PARAMETER_CODES, PARAM_MATURITY_ADJUSTMENT)

#: What the governed map is keyed by when the source states no counterparty type.
UNSTATED_COUNTERPARTY_TYPE = "UNSTATED"

#: The segment names the pure module knows. A governed map naming anything else
#: is a mapping error, not a new segment: the correlation for it does not exist.
KNOWN_SEGMENTS: frozenset[str] = frozenset(
    {SEGMENT_CORPORATE, SEGMENT_RETAIL_OTHER, SEGMENT_EXCLUDED}
)

# --- where a figure came from (carried onto the result, never into the sum) --
PD_SOURCE_EXPOSURE = "exposure"
PD_SOURCE_ECL_REGISTER = "ecl_register"
PD_SOURCE_PROXY = "proxy_by_risk_weight"
LGD_SOURCE_EXPOSURE = "exposure"
LGD_SOURCE_ECL_REGISTER = "ecl_register"
LGD_SOURCE_GOVERNED_DEFAULT = "governed_default"

# --- why a row is not in the book (each count is disclosed) ------------------
EXCLUDED_STAGE_3 = "ifrs9_stage_3"
EXCLUDED_ZERO_RISK_WEIGHT = "zero_risk_weighted"
EXCLUDED_SOVEREIGN_SEGMENT = "sovereign_segment"
EXCLUDED_UNCONVERTED = "unconverted"
EXCLUDED_NON_POSITIVE_EAD = "non_positive_ead"

# --- why the whole book could not be assembled -------------------------------
REFUSAL_PD_UNRESOLVED = "ga_pd_unresolved"
REFUSAL_SEGMENT_UNMAPPED = "ga_segment_unmapped"
REFUSAL_MATURITY_UNRESOLVED = "ga_maturity_unresolved"
#: No book was assembled at all. The adjustment needs every obligor's own EAD,
#: PD and segment, which is not something a preparer can type into a manual
#: entry form — so a cycle that supplies its figures by hand (a consolidated
#: one, D-018) cannot use this method and is told so, rather than being handed
#: an "empty book" that reads like a book with nothing in it.
REFUSAL_BOOK_NOT_SUPPLIED = "ga_book_not_supplied"

#: How many offending references a refusal names before it summarises. A display
#: depth: a refusal an operator cannot act on is only half a control.
SAMPLE_REFERENCES = 5

_ECL_SEGMENT_ALL = "ALL"
_ECL_STAGE_ONE = 1
_ATTR_PD_PCT = "pd_pct"
_ATTR_LGD_PCT = "lgd_pct"
_ATTR_RISK_WEIGHT_CODE = "risk_weight_code"
_ATTR_MATURITY_YEARS = "maturity_years"
_ATTR_MATURITY = "maturity"
_SEPARATOR = ":"


@dataclass(frozen=True)
class EclDefaults:
    """The Board-approved stage-1 (12-month) PD and LGD for segment ``ALL``."""

    pd_pct: Decimal | None = None
    lgd_pct: Decimal | None = None


@dataclass(frozen=True)
class GaBook:
    """The exposures the adjustment may measure, and what is not among them.

    ``refusals`` is empty on a book the method can measure. When it is not, the
    exposures are NOT handed to the formula: a partial book would produce a
    smaller, plausible figure and nothing on the result would say so.
    """

    exposures: tuple[GaExposure, ...]
    excluded: Mapping[str, int]
    refusals: tuple[str, ...]
    parameters_used: tuple[ParameterUse, ...]
    rows_read: int

    @property
    def complete(self) -> bool:
        return not self.refusals


#: The book of a cycle that never assembled one. It measures nothing and says
#: nothing was excluded, which is exactly what an absent book means; the method
#: then declines with ``ga_no_exposures`` rather than reading a missing book as
#: an empty one that computes to zero.
EMPTY_BOOK = GaBook(exposures=(), excluded={}, refusals=(), parameters_used=(), rows_read=0)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _ratio(percent: Decimal | None) -> Decimal | None:
    """A governed or ingested percentage as the ratio the formula consumes."""
    return None if percent is None else percent / HUNDRED


def ecl_defaults(db: Session, organization_id: str, bank: Bank, as_of: date) -> EclDefaults:
    """The ECL register's stage-1 row for segment ``ALL``, if the Board has one.

    Stage 1 only, and deliberately: a stage-2 row carries a LIFETIME PD, and the
    conditional loss this adjustment corrects is a one-year figure.
    """
    rows = get_active_params(db, organization_id, bank.jurisdiction_code, ParamEclAssumption, as_of)
    for row in rows:
        if row.segment == _ECL_SEGMENT_ALL and row.stage == _ECL_STAGE_ONE:
            return EclDefaults(pd_pct=_decimal(row.pd_pct), lgd_pct=_decimal(row.lgd_pct))
    return EclDefaults()


def _segment_map(resolved: params.P2Parameters) -> dict[str, str]:
    body = resolved.optional_body(PARAM_COUNTERPARTY_SEGMENT_MAP)
    if not body:
        raise MissingParameter(PARAM_COUNTERPARTY_SEGMENT_MAP)
    mapping = {str(key): str(value) for key, value in body.items() if key != KEY_SCHEMA}
    if not mapping:
        raise MissingParameter(PARAM_COUNTERPARTY_SEGMENT_MAP)
    unknown = sorted(set(mapping.values()) - KNOWN_SEGMENTS)
    if unknown:
        raise MissingParameter(
            PARAM_COUNTERPARTY_SEGMENT_MAP, detail=_SEPARATOR.join(unknown[:SAMPLE_REFERENCES])
        )
    return mapping


def _risk_weight_code(row: credit_exposure_book.ExposureRow) -> str | None:
    """The exposure's risk-weight code, resolved as the capital engine does it.

    An ingested attribute wins over the product register, which is the order
    ``enterprise_stress`` and the capital engine already use — so a row is
    classified the same way wherever it is read.
    """
    code = row.attributes.get(_ATTR_RISK_WEIGHT_CODE) or row.product_risk_weight_code
    return None if code is None else str(code)


def _maturity_years(row: credit_exposure_book.ExposureRow) -> Decimal | None:
    """An effective maturity in years, only if the source stated one."""
    for key in (_ATTR_MATURITY_YEARS, _ATTR_MATURITY):
        value = _decimal(row.attributes.get(key))
        if value is not None:
            return value
    return None


class _Resolver:
    """One pass over the book, keeping the counts a reader has to be told."""

    def __init__(
        self,
        *,
        resolved: params.P2Parameters,
        segments: Mapping[str, str],
        ecl: EclDefaults,
        maturity_required: bool,
    ) -> None:
        self._resolved = resolved
        self._segments = segments
        self._ecl = ecl
        self._maturity_required = maturity_required
        self.excluded: dict[str, int] = {}
        self.unresolved_pd: list[str] = []
        self.unmapped_segments: list[str] = []
        self.unresolved_maturity: list[str] = []
        self.used_proxy_pd = False
        self.used_default_elgd = False

    def _exclude(self, reason: str) -> None:
        self.excluded[reason] = self.excluded.get(reason, 0) + 1

    def _pd(self, row: credit_exposure_book.ExposureRow) -> tuple[Decimal, str] | None:
        stated = _ratio(_decimal(row.attributes.get(_ATTR_PD_PCT)))
        if stated is not None:
            return stated, PD_SOURCE_EXPOSURE
        registered = _ratio(self._ecl.pd_pct)
        if registered is not None:
            return registered, PD_SOURCE_ECL_REGISTER
        code = _risk_weight_code(row)
        if code is None:
            return None
        table = self._resolved.optional_body(PARAM_PROXY_PD_BY_RW_CODE)
        if table is None:
            raise MissingParameter(PARAM_PROXY_PD_BY_RW_CODE, detail=code)
        proxy = _ratio(_decimal(table.get(code)))
        if proxy is None:
            return None
        self.used_proxy_pd = True
        return proxy, PD_SOURCE_PROXY

    def _elgd(self, row: credit_exposure_book.ExposureRow) -> tuple[Decimal, str]:
        stated = _ratio(_decimal(row.attributes.get(_ATTR_LGD_PCT)))
        if stated is not None:
            return stated, LGD_SOURCE_EXPOSURE
        registered = _ratio(self._ecl.lgd_pct)
        if registered is not None:
            return registered, LGD_SOURCE_ECL_REGISTER
        governed = _ratio(self._resolved.optional_decimal(PARAM_DEFAULT_ELGD_PCT))
        if governed is None:
            raise MissingParameter(PARAM_DEFAULT_ELGD_PCT)
        self.used_default_elgd = True
        return governed, LGD_SOURCE_GOVERNED_DEFAULT

    def exposure(  # noqa: PLR0911 - one return per named exclusion or refusal
        self, row: credit_exposure_book.ExposureRow
    ) -> GaExposure | None:
        """One canonical row as an obligor exposure, or the reason it is not."""
        if row.credit_impaired:
            self._exclude(EXCLUDED_STAGE_3)
            return None
        if _risk_weight_code(row) == ZERO_RISK_WEIGHT_CODE:
            self._exclude(EXCLUDED_ZERO_RISK_WEIGHT)
            return None
        segment = self._segments.get(row.counterparty_type or UNSTATED_COUNTERPARTY_TYPE)
        if segment is None:
            self.unmapped_segments.append(row.source_reference)
            return None
        if segment == SEGMENT_EXCLUDED:
            self._exclude(EXCLUDED_SOVEREIGN_SEGMENT)
            return None
        if row.unconverted:
            self._exclude(EXCLUDED_UNCONVERTED)
            return None
        ead = row.balance_rep
        if ead is None or ead <= Decimal(0):
            self._exclude(EXCLUDED_NON_POSITIVE_EAD)
            return None
        resolved_pd = self._pd(row)
        if resolved_pd is None:
            self.unresolved_pd.append(row.source_reference)
            return None
        elgd, lgd_source = self._elgd(row)
        maturity = _maturity_years(row)
        if self._maturity_required and maturity is None:
            self.unresolved_maturity.append(row.source_reference)
            return None
        pd_value, pd_source = resolved_pd
        return GaExposure(
            ref=row.source_reference,
            group_key=row.group_key,
            ead=ead,
            pd=pd_value,
            elgd=elgd,
            segment=_as_segment(segment),
            maturity_years=maturity,
            pd_source=pd_source,
            lgd_source=lgd_source,
        )


def _as_segment(segment: str) -> Segment:
    """Narrow a governed segment name onto the pure module's closed vocabulary.

    ``_segment_map`` has already refused anything outside it, so this is a type
    narrowing rather than a check.
    """
    return SEGMENT_RETAIL_OTHER if segment == SEGMENT_RETAIL_OTHER else SEGMENT_CORPORATE


def _refusal(code: str, references: Sequence[str]) -> str:
    """A machine reason carrying the count and a sample of what caused it."""
    sample = _SEPARATOR.join(sorted(references)[:SAMPLE_REFERENCES])
    return f"{code}{_SEPARATOR}{len(references)}{_SEPARATOR}{sample}"


def build_book(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> GaBook:
    """Assemble the obligor book for this cycle's as-of date."""
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=_RESOLVED_CODES)
    maturity = parse_maturity_adjustment(resolved.optional_body(PARAM_MATURITY_ADJUSTMENT))
    rows = credit_exposure_book.load_exposure_rows(
        db,
        access.ctx,
        access.bank,
        cycle.as_of_date,
        credit_exposure_book.CONCENTRATION_POSITION_TYPES,
    )
    resolver = _Resolver(
        resolved=resolved,
        segments=_segment_map(resolved),
        ecl=ecl_defaults(db, access.ctx.organization_id, access.bank, cycle.as_of_date),
        maturity_required=maturity.apply,
    )
    exposures = tuple(exposure for row in rows if (exposure := resolver.exposure(row)) is not None)

    refusals: list[str] = []
    if resolver.unmapped_segments:
        refusals.append(_refusal(REFUSAL_SEGMENT_UNMAPPED, resolver.unmapped_segments))
    if resolver.unresolved_pd:
        refusals.append(_refusal(REFUSAL_PD_UNRESOLVED, resolver.unresolved_pd))
    if resolver.unresolved_maturity:
        refusals.append(_refusal(REFUSAL_MATURITY_UNRESOLVED, resolver.unresolved_maturity))

    uses = [
        ParameterUse(PARAM_COUNTERPARTY_SEGMENT_MAP, "counterparty segment"),
        ParameterUse(PARAM_MATURITY_ADJUSTMENT, "maturity requirement"),
    ]
    if resolver.used_proxy_pd:
        uses.append(ParameterUse(PARAM_PROXY_PD_BY_RW_CODE, "proxy pd"))
    if resolver.used_default_elgd:
        uses.append(ParameterUse(PARAM_DEFAULT_ELGD_PCT, "default elgd"))

    return GaBook(
        exposures=() if refusals else exposures,
        excluded=dict(sorted(resolver.excluded.items())),
        refusals=tuple(refusals),
        parameters_used=tuple(uses),
        rows_read=len(rows),
    )


__all__ = [
    "BOOK_PARAMETER_CODES",
    "EXCLUDED_NON_POSITIVE_EAD",
    "EXCLUDED_SOVEREIGN_SEGMENT",
    "EXCLUDED_STAGE_3",
    "EXCLUDED_UNCONVERTED",
    "EXCLUDED_ZERO_RISK_WEIGHT",
    "KNOWN_SEGMENTS",
    "LGD_SOURCE_ECL_REGISTER",
    "LGD_SOURCE_EXPOSURE",
    "LGD_SOURCE_GOVERNED_DEFAULT",
    "PD_SOURCE_ECL_REGISTER",
    "PD_SOURCE_EXPOSURE",
    "PD_SOURCE_PROXY",
    "REFUSAL_BOOK_NOT_SUPPLIED",
    "REFUSAL_MATURITY_UNRESOLVED",
    "REFUSAL_PD_UNRESOLVED",
    "REFUSAL_SEGMENT_UNMAPPED",
    "SAMPLE_REFERENCES",
    "UNSTATED_COUNTERPARTY_TYPE",
    "EMPTY_BOOK",
    "EclDefaults",
    "GaBook",
    "build_book",
    "ecl_defaults",
]
