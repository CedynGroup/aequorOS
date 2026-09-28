"""The curated feed registry: the only datasets a report server may pull.

``docs/bi.md`` §Phase 4 asks for "curated datasets", and the word is the whole
design. A report server does not name a table, a column list, a filter or
anything resembling SQL; it names one of the datasets DECLARED here, and the
platform decides what that means. Every property a pull needs is on the
declaration:

* the catalogue members it is built from — so the authorization sentence, the
  disclosure class and the column list are all derived from the same catalogue
  every interactive BI surface is governed by, and none of them is a second
  definition of the same figure;
* the grain, in production copy, so the consumer's model can state its own key;
* the mart build scopes it reads, which is what makes the cursor correct
  (``cursor.py``): a slice is servable only when every scope the dataset reads
  has succeeded for that reporting date, and it is re-served whenever any of
  them is rebuilt;
* the disclosure class it claims, which is checked against the class the
  catalogue's own sensitivities imply (:func:`declared_class_matches`). A
  declaration cannot understate what a dataset discloses.

**Every registered dataset is ``summary``, and that is deliberate.** The
``bi_reader`` bundle carries ``Permission.VIEW`` and nothing else
(``app/core/authorization.py``), so a record-level dataset — one naming an
obligor, an employer or a single position — would need ``Permission.EXPORT``
that no machine bundle grants, and would therefore be registered but
unservable: an inert feature wearing the badge of a shipped one. A named-obligor
extract leaving the building on a machine credential nobody is holding is also
the disclosure the bundle was scoped to exclude. So the registry is aggregated
by construction, ``test_feed_datasets`` asserts it, and adding a record-level
dataset fails that assertion — which is the decision point, not an obstacle.

**There is no flow dataset yet.** ``events.*`` are ``flow`` measures and need a
WINDOW rather than an as-of date, and the loan-event fact is keyed by event date
while a mart build is keyed by reporting date, so the slice cursor below cannot
say what "new" means for it. Named here rather than half-built; a flow feed needs
its own cursor anchored on the event fact's own build bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final

from app.domain.bi.catalogue import Catalogue
from app.models.bi import MART_BUILD_SCOPES
from app.schemas.bi import BiQuery, BiSort, BiTime
from app.services.bi.authorization import (
    bank_wide_measures,
    query_members,
)
from app.services.bi.authorization import institution_grain_measures as grain_measures
from app.services.bi.exports import policy


class UnknownDataset(Exception):  # noqa: N818 - a refusal, raised and rendered as 404
    """The caller named a dataset the registry does not declare.

    Carries the id it was GIVEN and never the registry: a machine credential
    probing for dataset names must learn nothing from the refusal but that this
    one is not served (the route answers 404 with no list).
    """

    def __init__(self, dataset_id: str) -> None:
        super().__init__(f"unknown BI feed dataset: {dataset_id}")
        self.dataset_id = dataset_id


@dataclass(frozen=True, slots=True)
class FeedDataset:
    """One curated dataset: what it is, what it reads, and what it discloses."""

    id: str
    title: str
    #: What ONE ROW is, in production copy. The consumer's primary key.
    grain: str
    #: Catalogue dimension ids, in column order. The first is always the
    #: reporting date: a feed row that cannot say which date it belongs to
    #: cannot be replaced by a restatement of that date.
    dimensions: tuple[str, ...]
    #: Catalogue measure ids, in column order.
    measures: tuple[str, ...]
    #: Mart build scopes this dataset's rows come from. Every one must have
    #: SUCCEEDED for a reporting date before that date is servable.
    build_scopes: tuple[str, ...]
    #: The class the dataset claims, checked against the catalogue's own
    #: sensitivities by :func:`declared_class_matches`.
    disclosure_class: policy.ExportClass

    @property
    def member_ids(self) -> tuple[str, ...]:
        """Every member id named on the declaration, dimensions first."""

        return (*self.dimensions, *self.measures)

    def query(self, as_of: date) -> BiQuery:
        """This dataset as the compiler takes it, for ONE reporting date.

        Sorted by every dimension so the statement has a TOTAL order: the rows
        are grouped by exactly those columns, so the tuple is unique per row,
        and a total order is what makes the paged read in ``runner.py`` able to
        walk a slice without repeating or skipping a row.
        """

        return BiQuery(
            measures=list(self.measures),
            dimensions=list(self.dimensions),
            time=BiTime(as_of=as_of),
            sort=[BiSort(member=dimension, direction="asc") for dimension in self.dimensions],
        )


#: The reporting-date dimension every dataset leads with.
REPORTING_DATE_DIMENSION: Final = "time.date"

#: The date the SHAPE of a dataset is read at. A dataset's member walk, its
#: disclosure class and its column list are the same whichever reporting date the
#: query is pointed at, so everything that needs the shape rather than the data
#: uses this one fixed date instead of threading a meaningless argument — and
#: never the clock, which would make the shape a function of when it was asked.
SHAPE_DATE: Final = date(2026, 1, 1)

_REGISTRY: tuple[FeedDataset, ...] = (
    FeedDataset(
        id="loan_book",
        title="Loan book by branch, product and sector",
        grain=(
            "One row per reporting date, branch, product family, economic sector "
            "and impairment stage."
        ),
        dimensions=(
            REPORTING_DATE_DIMENSION,
            "branch.code",
            "product.family",
            "loan.sector",
            "loan.ifrs9_stage",
        ),
        measures=(
            "loans.balance_rc",
            "loans.npl_exposure_rc",
            "loans.par_90_exposure_rc",
            "loans.provision_required_rc",
            "loans.provision_held_rc",
            "loans.collateral_rc",
            "loans.count",
        ),
        build_scopes=("positions", "dims"),
        disclosure_class=policy.SUMMARY,
    ),
    FeedDataset(
        id="deposit_book",
        title="Deposit book by branch, product and maturity",
        grain=(
            "One row per reporting date, branch, product family, account type and maturity bucket."
        ),
        dimensions=(
            REPORTING_DATE_DIMENSION,
            "branch.code",
            "product.family",
            "position.deposit_account_type",
            "position.maturity_bucket",
        ),
        measures=(
            "deposits.balance_rc",
            "deposits.demand_balance_rc",
            "deposits.count",
        ),
        build_scopes=("positions", "dims"),
        disclosure_class=policy.SUMMARY,
    ),
    FeedDataset(
        id="regulatory_metrics",
        title="Regulatory ratios, filed and live",
        grain="One row per reporting date, for the whole institution.",
        dimensions=(REPORTING_DATE_DIMENSION,),
        measures=(
            "engine.car_pct.crd.official",
            "engine.tier1_ratio_pct.crd.official",
            "engine.cet1_ratio_pct.crd.official",
            "engine.leverage_ratio_pct.crd.official",
            "engine.lcr_pct.crd.official",
            "engine.nsfr_pct.crd.official",
            "engine.worst_eve_change_pct_tier1.crd.official",
            "engine.nop_pct_tier1.crd.official",
            "engine.car_pct.crd.live",
            "engine.tier1_ratio_pct.crd.live",
            "engine.cet1_ratio_pct.crd.live",
            "engine.leverage_ratio_pct.crd.live",
            "engine.lcr_pct.crd.live",
            "engine.nsfr_pct.crd.live",
            "engine.worst_eve_change_pct_tier1.crd.live",
            "engine.nop_pct_tier1.crd.live",
        ),
        build_scopes=("engine", "dims"),
        disclosure_class=policy.SUMMARY,
    ),
)

DATASETS: Final[MappingProxyType[str, FeedDataset]] = MappingProxyType(
    {entry.id: entry for entry in _REGISTRY}
)


def dataset(dataset_id: str) -> FeedDataset:
    """The declaration for ``dataset_id``, or :class:`UnknownDataset`."""

    try:
        return DATASETS[dataset_id]
    except KeyError as exc:
        raise UnknownDataset(dataset_id) from exc


def dataset_ids() -> tuple[str, ...]:
    """Every registered dataset id, in registry order."""

    return tuple(DATASETS)


def declared_class_matches(cat: Catalogue, entry: FeedDataset) -> bool:
    """Whether the declared disclosure class is the one the catalogue implies.

    Deny-by-default in the only direction that matters: a dataset may not
    declare ``summary`` over a member the catalogue calls ``restricted`` or
    ``confidential``. The comparison is EQUALITY rather than "at least as
    strict", because a dataset declaring a class stricter than its members need
    would demand an authority no machine bundle carries and could never be
    served — an inert registration, which is the other failure this checks for.
    """

    members = query_members(cat, entry.query(SHAPE_DATE))
    return policy.classify(members) == entry.disclosure_class


def institution_grain_measures(cat: Catalogue, entry: FeedDataset) -> tuple[str, ...]:
    """The dataset's measures a scoped credential may not be served.

    Two classes, not one (audit A10-07). Both are figures that are the
    INSTITUTION'S, so a branch slice of either is a wrong number with a
    right-looking name, and a dataset naming one needs a binding of data scope
    ``all`` (``authorization.py``):

    * ``grain == "institution"`` — an institution ratio such as capital adequacy.
    * a BANK-WIDE target figure — the target row is stated for the whole bank, so
      serving it beside one branch's actual yields an attainment of a branch
      against the institution's budget.

    Only the first was checked here, which was correct for the three datasets
    registered today and wrong the moment a target-bearing dataset is added — the
    interactive path has refused both classes since Phase 4
    (``bi.authorization`` ``REASON_INSTITUTION_GRAIN`` / ``REASON_BANK_WIDE_FIGURE``).
    Both are read from the shared helpers rather than re-derived, so the feed and
    the interactive surfaces cannot drift into two different answers about the
    same measure.
    """

    members = tuple(cat.member(measure_id) for measure_id in entry.measures)
    return (*grain_measures(members), *bank_wide_measures(members))


def unknown_build_scopes() -> tuple[str, ...]:
    """Build scopes named by the registry that the mart does not record.

    A dataset waiting on a scope nobody writes would never be servable and
    would report "no new data" forever, which is indistinguishable from a bank
    that has not ingested. Pinned by a test rather than trusted.
    """

    declared = {scope for entry in _REGISTRY for scope in entry.build_scopes}
    return tuple(sorted(declared - set(MART_BUILD_SCOPES)))


__all__ = [
    "DATASETS",
    "REPORTING_DATE_DIMENSION",
    "SHAPE_DATE",
    "FeedDataset",
    "UnknownDataset",
    "dataset",
    "dataset_ids",
    "declared_class_matches",
    "institution_grain_measures",
    "unknown_build_scopes",
]
