"""A declared data scope, resolved against the branches the mart actually holds.

``authorize_query`` answers *which slice* as the bindings DECLARE it: a set of
branch codes, a set of region names, or the whole institution
(``authorization.BiDataScope``). This module is the other half — it turns that
sentence into the ONE filter the compiler ANDs into every statement it builds,
and it is the only place in the platform that reads ``bi_dim_branch.region``.
The account plane deliberately cannot: BI is a dispatch plane and the dependency
runs one way only, so ``services.authorization`` returns the declared scope and
the resolution happens here.

Four properties, each of which is a way this goes wrong:

1. **The filter is unremovable because the client never touches it.** It is
   passed to ``compile_query`` beside the ``BiQuery``, not inside it, and the
   compiler ANDs it with the client's own predicates. A request that filters
   ``branch.code = 'B2'`` while the grant says ``B1`` is served the
   INTERSECTION — nothing — not its own value.
2. **A scope that resolves to nothing serves nothing, never everything.** The
   natural reader (``if codes: filter``) would hand an empty resolution the whole
   book, which is why the stored column refuses an empty value list at all
   (migration ``202609270073``) and why the two remaining empty cases are named
   here: a region grant no ingested branch belongs to, and a granted branch code
   the Data Engine has never seen. The first is filtered on the REGION, which by
   definition matches no row; the second is filtered on the code, which matches
   no row either. Both serve zero rows and neither can fall through to ``()``.
3. **A branch ingested after the grant joins its region.** The resolution runs
   per query, in the reading transaction, so a region grant covers whatever the
   region contains now. That is what a region grant MEANS and is not a leak.
4. **A scope is part of the answer's identity.** ``fingerprint`` goes into the
   ETag, so a principal whose grant narrows between two reads cannot be served
   the wider answer out of their own cache, and two principals asking the same
   question under different scopes cannot share a representation.

``label`` is what the export provenance block prints. A spreadsheet of one
region's book that looks institution-wide is exactly the misreading that block
exists to prevent, so the label names the slice and, for a region grant, how many
branches it came to — including "no branches", which is the honest thing to print
when the answer is empty.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bi import BiDimBranch
from app.schemas.bi import BI_FILTER_IN_CAP, BiFilter
from app.services.bi.authorization import BiDataScope, DataScopeKind
from app.services.bi.errors import BiQueryError

#: The dimension the resolved scope filters on, and the one it falls back to when
#: a region grant resolves to no branch at all. Both are catalogue member ids
#: (``domain/bi/catalogue/dimensions.py``) and both are in every branch-sliceable
#: measure's ``allowed_dimensions``, which is what makes either safe to inject.
BRANCH_CODE_MEMBER = "branch.code"
BRANCH_REGION_MEMBER = "branch.region"

#: How many scope values the label prints before it summarises. A grant naming
#: two hundred branches must not turn a PDF's provenance block into a page.
_LABEL_VALUE_CAP = 12


class DataScopeUnservable(BiQueryError):
    """The scope cannot be expressed as one filter this query path accepts.

    Reachable only when a grant covers more branches than a single ``IN`` list
    may carry (:data:`app.schemas.bi.BI_FILTER_IN_CAP`). Refused rather than
    truncated or dropped: either of those would serve rows outside the scope or
    the whole book.
    """

    status_code = 422
    code = "bi_data_scope_unservable"


class DataScopeServesNothing(RuntimeError):
    """An allowed decision carried ``kind="none"``, which cannot happen.

    ``authorize_query`` denies that case explicitly, so reaching here means a
    caller built a decision by hand or a new path skipped the check. It raises
    rather than returning no filters, because returning ``()`` from here is
    precisely "serve the whole institution".

    Deliberately NOT a :class:`BiQueryError`: every serving surface turns one of
    those into a refusal it records and moves on from, and this is an invariant
    violation that must reach the error log as a 500 rather than be filed as
    "the query was refused".
    """


@dataclass(frozen=True, slots=True)
class ResolvedDataScope:
    """One principal's declared scope, resolved against this institution's mart."""

    kind: DataScopeKind
    #: Branch codes the grant named, verbatim. A code the Data Engine has never
    #: ingested stays in the set on purpose: it then matches no fact row, which
    #: is the honest answer, whereas dropping it would silently widen the filter.
    declared_branches: tuple[str, ...] = ()
    #: Region names the grant named, verbatim.
    declared_regions: tuple[str, ...] = ()
    #: The codes the filter names: the declared codes plus every branch the mart
    #: holds in a declared region. Empty ONLY when the scope is regions alone and
    #: no ingested branch sits in any of them.
    branch_codes: tuple[str, ...] = ()

    @property
    def whole_institution(self) -> bool:
        return self.kind == "all"

    @property
    def serves_nothing(self) -> bool:
        return self.kind == "none"

    @property
    def filters(self) -> tuple[BiFilter, ...]:
        """The scope as filters the request cannot reach, remove or widen."""

        if self.whole_institution:
            return ()
        if self.serves_nothing:
            raise DataScopeServesNothing(
                "This read was authorized under no binding, so there is no slice to serve."
            )
        if self.branch_codes:
            self._require_servable(self.branch_codes, "branches")
            return (BiFilter(member=BRANCH_CODE_MEMBER, op="in", values=list(self.branch_codes)),)
        # Regions alone, none of which any ingested branch belongs to. Filtering
        # on the REGION is the same sentence the grant states and matches no row,
        # so the scope serves nothing without a sentinel value standing in for
        # "impossible code".
        self._require_servable(self.declared_regions, "regions")
        return (BiFilter(member=BRANCH_REGION_MEMBER, op="in", values=list(self.declared_regions)),)

    @property
    def label(self) -> str:
        """Production copy naming the slice, for an artifact's provenance block."""

        if self.whole_institution:
            return "Whole institution"
        if self.serves_nothing:
            return "No data in scope"
        parts: list[str] = []
        if self.declared_branches:
            parts.append(f"Branches: {_values_label(self.declared_branches)}")
        if self.declared_regions:
            parts.append(f"Regions: {_values_label(self.declared_regions)}")
            parts.append(_branch_count_label(len(self.branch_codes)))
        return " · ".join(parts)

    @property
    def fingerprint(self) -> str:
        """A digest of everything about the scope that can change the answer."""

        material = "\x1f".join(
            (
                self.kind,
                ",".join(self.declared_branches),
                ",".join(self.declared_regions),
                ",".join(self.branch_codes),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _require_servable(self, values: tuple[str, ...], noun: str) -> None:
        if len(values) > BI_FILTER_IN_CAP:
            raise DataScopeUnservable(
                f"Your access covers {len(values)} {noun}; at most {BI_FILTER_IN_CAP} "
                "can be applied to one question. Ask for a narrower grant, or for "
                "institution-wide access."
            )


#: The scope of a principal holding at least one institution-wide sentence.
WHOLE_INSTITUTION = ResolvedDataScope(kind="all")


def _values_label(values: tuple[str, ...]) -> str:
    if len(values) <= _LABEL_VALUE_CAP:
        return ", ".join(values)
    shown = ", ".join(values[:_LABEL_VALUE_CAP])
    return f"{shown} and {len(values) - _LABEL_VALUE_CAP} more"


def _branch_count_label(count: int) -> str:
    if count == 0:
        return "no branches in scope"
    if count == 1:
        return "1 branch in scope"
    return f"{count} branches in scope"


def resolve(
    db: Session, scope: BiDataScope, *, organization_id: str, bank_id: str
) -> ResolvedDataScope:
    """Resolve a declared scope against ONE institution's branch dimension.

    Scoped to the exact ``(organization, bank)``: two banks of one organization
    share an RLS tenant, so organization scoping alone would let one bank's
    region grant pick up the other's branch codes.
    """

    if scope.whole_institution:
        return WHOLE_INSTITUTION
    if scope.serves_nothing:
        return ResolvedDataScope(kind="none")
    codes = set(scope.branches)
    if scope.regions:
        codes.update(
            db.scalars(
                select(BiDimBranch.branch_code).where(
                    BiDimBranch.organization_id == organization_id,
                    BiDimBranch.bank_id == bank_id,
                    BiDimBranch.region.in_(set(scope.regions)),
                )
            )
        )
    return ResolvedDataScope(
        kind=scope.kind,
        declared_branches=scope.branches,
        declared_regions=scope.regions,
        branch_codes=tuple(sorted(codes)),
    )


# ``scope_filters`` was removed 2026-09-28 (audit A10-06). It wrapped
# ``resolve(...).filters`` and its docstring asserted "Every surface that compiles
# a query for a principal uses this", which was false: it had ZERO callers and no
# tests, while all six surfaces called ``resolve`` directly. There was no
# behavioural defect — they use the same authority — but a dead function carrying a
# false claim about a security seam is worse than no function, because the next
# reader trusts the sentence and stops looking. ``resolve`` IS the seam; if a
# one-call convenience is ever wanted, add it with callers and a test.

__all__ = [
    "BRANCH_CODE_MEMBER",
    "BRANCH_REGION_MEMBER",
    "WHOLE_INSTITUTION",
    "DataScopeServesNothing",
    "DataScopeUnservable",
    "ResolvedDataScope",
    "resolve",
]
