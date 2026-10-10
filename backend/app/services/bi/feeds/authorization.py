"""Deny-by-default authorization for ONE feed pull, by a MACHINE principal.

``app/services/bi/authorization.py`` refuses a machine key outright and says why:
only an interactive human reads the BI surfaces, and "the Phase 4 feed route has
its own machine dependency; this function never authorizes one". This is that
dependency's decision, and it is deliberately the same SHAPE as the human one so
the two cannot drift into different answers about the same figure:

1. **The credential must be the one this route is for.** An ``aeq_live_…``
   integration key, issued for EXACTLY this institution, not an app token, not an
   impersonated operator session. A human bearer token is refused here even when
   its holder could run the identical query interactively — a feed is a machine
   surface and the credential is the audit trail.
2. **The licence class must carry the module**, per member module, exactly as
   ``require_module_access`` gates the routers and ``authorize_query`` gates a
   query.
3. **Every distinct ``(module, sensitivity)`` pair the dataset touches is
   evaluated**, all-or-exact on both, with no ladder of its own — the pairs come
   from the catalogue's own declarations via ``query_members`` / ``scope_pairs``,
   so the dataset's columns and the sentences it needs cannot disagree.
4. **The data scope applies, through the platform's ONE resolver, reduced PER
   PAIR.** The bindings that authorized each pair are kept apart and reduced
   through ``authorization.combine_pair_scopes`` — the same helper
   ``authorize_query`` uses — so an ``all`` sentence that authorized the
   ``risk``/``aggregated`` date and branch dimensions cannot discard the branch
   restriction that applies to the credit measures (audit A360-1 H8: this module
   once unioned the ids across pairs and reduced once, and a second reader
   binding scoped ``risk/aggregated/all`` widened a ``branch=["B2"]`` key to the
   whole institution, header included). ``services/bi/data_scope.py`` then turns
   the declared sentence into the single unremovable filter the compiler ANDs in
   — the same seam the read routes, the export job, a subscription render and an
   alert evaluation use, so there is one answer to "what does this grant mean in
   SQL" rather than five. The ONE thing the feed decides for itself is what to
   do when the scope resolves to no branch: see :func:`authorize_feed`.
5. **An institution ratio needs the whole institution.** A branch slice of a
   capital ratio is a wrong number with a right-looking name, so a dataset naming
   an institution-grain measure is refused to a scoped credential outright.

**Why a writer key cannot reach this and a reader key cannot reach the push
routes**, without either route naming the other's bundle: the two machine bundles
carry disjoint permissions. ``integration_writer`` is ``{INGEST}`` and this
function asks for ``VIEW``; ``bi_reader`` is ``{VIEW}`` and
``require_integration_push_ingest`` asks for ``INGEST``. Neither refusal depends
on a bundle name being checked in the right place, which is the kind of check
that gets moved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    Sensitivity,
)
from app.core.observability import authorization_denied
from app.domain.bi.catalogue import Catalogue
from app.identity.service import authorization as authorization_service
from app.models import Bank
from app.schemas.bi import BiFilter
from app.services import institution_types
from app.services.bi import data_scope as scope_resolver
from app.services.bi.authorization import (
    ENTITLEMENT_SLUGS,
    NO_INSTITUTION_DATA,
    REASON_ALLOWED,
    REASON_EVALUATION_FAILED,
    REASON_NO_AUTHORIZING_BINDING,
    REASON_NOT_ENTITLED,
    REASON_SCOPE_UNRECOGNIZED,
    BiDataScope,
    combine_pair_scopes,
    query_members,
    scope_pairs,
)
from app.services.bi.feeds.datasets import (
    SHAPE_DATE,
    FeedDataset,
    institution_grain_measures,
)

#: The ``bi_query_log`` surface every pull is recorded under. Already in the
#: table's own vocabulary (``models.bi.QUERY_LOG_SURFACES``) and in the database
#: CHECK since ``202609220066``; a test pins this constant against both.
SURFACE_FEED: Final = "feed"

#: Telemetry/log reasons. The four shared ones are imported rather than restated
#: so one telemetry filter catches a feed refusal and an interactive one alike.
REASON_MACHINE_REQUIRED: Final = "machine_feed_credential_required"
REASON_INSTITUTION_GRAIN: Final = "institution_grain_requires_whole_institution"
REASON_SCOPE_MATCHES_NO_BRANCH: Final = "data_scope_matches_no_branch"
#: Every pair allowed and yet no effective binding is left to serve under. The
#: interactive path's own string, not a feed-specific restatement: the condition
#: is decided by the shared ``combine_pair_scopes`` for both surfaces, so the
#: telemetry filter that catches one must catch the other.
REASON_NO_DATA_SCOPE: Final = REASON_NO_AUTHORIZING_BINDING


@dataclass(frozen=True, slots=True)
class FeedAuthorization:
    """The decision for one pull: what may be served, over which slice."""

    allowed: bool
    reason: str
    #: Every catalogue member the dataset touches, expansions included.
    member_ids: tuple[str, ...]
    #: The complete refused set — member IDS only, never a value or a row.
    denied_members: tuple[str, ...]
    matching_binding_ids: tuple[UUID, ...]
    #: The DECLARED slice, combined across every pair the dataset touches.
    data_scope: BiDataScope = NO_INSTITUTION_DATA
    #: The unremovable filter the compiler receives, separately from any client
    #: input, because the client sends no query at all.
    injected: tuple[BiFilter, ...] = field(default_factory=tuple)
    #: The declared scope resolved against this institution's branch dimension.
    resolved: scope_resolver.ResolvedDataScope = scope_resolver.WHOLE_INSTITUTION

    @property
    def scope_label(self) -> str:
        """The slice this pull covers, in the platform's own production copy.

        A report server whose dataset silently changed shape is worse than one
        that was refused, so the covered slice travels with every response.
        """

        return self.resolved.label


def machine_credential(ctx: TenantContext, bank: Bank) -> bool:
    """Whether the principal is a bank-scoped machine key for THIS institution.

    ``authorization_version is None`` is the positive test that this is a key and
    not an app token: an app token always carries one and an integration key
    never does (``api/deps.py``). Impersonation is named explicitly so a future
    context shape that carried a key id under an operator session is still
    refused here, not merely upstream.
    """

    return (
        ctx.integration_key_id is not None
        and ctx.integration_key_bank_id == bank.id
        and ctx.actor_user_id is not None
        and ctx.authorization_version is None
        and ctx.impersonation_context is None
    )


def _deny(
    reason: str,
    *,
    member_ids: tuple[str, ...],
    denied: tuple[str, ...] | None = None,
) -> FeedAuthorization:
    return FeedAuthorization(
        allowed=False,
        reason=reason,
        member_ids=member_ids,
        denied_members=member_ids if denied is None else denied,
        matching_binding_ids=(),
    )


@dataclass(frozen=True, slots=True)
class _EvaluatedPairs:
    """What the evaluator said about every pair, with the ids kept APART."""

    denied: tuple[str, ...]
    reasons: tuple[str, ...]
    #: Every id that authorized anything, for the decision's audit trail and the
    #: query log. NOT what the scope is reduced from.
    matched: tuple[UUID, ...]
    #: The ids that authorized EACH pair, one tuple per allowed pair, because the
    #: reduction is sound only within one pair (A10-01 / A360-1 H8; see
    #: ``authorization.combine_pair_scopes``).
    per_pair: tuple[tuple[UUID, ...], ...]


def _evaluate_pairs(  # noqa: PLR0913 - one telemetry keyword beside the sentence's parts
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    pairs: Mapping[tuple[str, str], tuple[str, ...]],
    *,
    telemetry_surface: str,
) -> _EvaluatedPairs:
    """Ask the evaluator for ``VIEW`` on every pair, as the machine principal.

    Entitlement (the licence class carries the module) is checked before the
    evaluator is asked, exactly as ``authorize_query`` does; an unrecognised
    catalogue scope, an unentitled module, an evaluator failure and a plain
    refusal each deny the pair's members with a named reason.
    """

    assert ctx.actor_user_id is not None  # noqa: S101 - ``machine_credential`` required it
    entitled = frozenset(institution_types.get_type(db, bank).default_modules)
    denied: list[str] = []
    reasons: list[str] = []
    matched: dict[UUID, None] = {}
    per_pair: list[tuple[UUID, ...]] = []
    principal = PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.MACHINE)

    for (module_value, sensitivity_value), pair_members in pairs.items():
        slug = ENTITLEMENT_SLUGS.get(module_value)
        try:
            module, sensitivity = Module(module_value), Sensitivity(sensitivity_value)
        except ValueError:
            authorization_denied(
                reason=REASON_SCOPE_UNRECOGNIZED,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                module=module_value,
                sensitivity=sensitivity_value,
                surface=telemetry_surface,
            )
            denied.extend(pair_members)
            reasons.append(REASON_SCOPE_UNRECOGNIZED)
            continue
        if slug is None or slug not in entitled:
            authorization_denied(
                reason=REASON_NOT_ENTITLED,
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                module=module_value,
                surface=telemetry_surface,
            )
            denied.extend(pair_members)
            reasons.append(REASON_NOT_ENTITLED)
            continue
        resource = ResourceLocator(
            ctx.organization_id,
            InstitutionScope.INSTITUTION,
            bank.id,
            module,
            sensitivity,
        )
        try:
            decision = authorization_service.evaluate_permission(
                db, principal, Permission.VIEW, resource
            )
            authorization_service.record_binding_decision(
                decision,
                surface=telemetry_surface,
                severity="info" if decision.allowed else "warning",
            )
        except Exception as exc:  # noqa: BLE001 - enforcement must deny on evaluator failure
            authorization_service.record_binding_evaluation_failure(
                principal, Permission.VIEW, resource, surface=telemetry_surface, error=exc
            )
            denied.extend(pair_members)
            reasons.append(REASON_EVALUATION_FAILED)
            continue
        if not decision.allowed:
            denied.extend(pair_members)
            reasons.append(decision.reason)
            continue
        matched.update(dict.fromkeys(decision.matching_binding_ids))
        per_pair.append(tuple(decision.matching_binding_ids))
    return _EvaluatedPairs(tuple(denied), tuple(reasons), tuple(matched), tuple(per_pair))


def authorize_feed(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    cat: Catalogue,
    entry: FeedDataset,
) -> FeedAuthorization:
    """Require every sentence the dataset needs, for one already-resolved bank.

    ``bank`` must come from the tenant-scoped resolver, so a sibling tenant's
    ``BK-*`` is 404 before this runs. Nothing here raises: the caller renders the
    decision as a stream or as a refusal, and writes exactly one query-log row
    either way.
    """

    members = query_members(cat, entry.query(SHAPE_DATE))
    member_ids = tuple(member.id for member in members)
    telemetry_surface = f"bi_{SURFACE_FEED}"

    if not machine_credential(ctx, bank) or ctx.actor_user_id is None:
        # ``machine_credential`` already requires an acting service identity; the
        # second clause is what lets the locator below be typed, and it denies
        # rather than asserting so a future context shape cannot crash the route.
        authorization_denied(
            reason=REASON_MACHINE_REQUIRED,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            dataset=entry.id,
        )
        return _deny(REASON_MACHINE_REQUIRED, member_ids=member_ids)

    evaluated = _evaluate_pairs(
        db, ctx, bank, scope_pairs(members), telemetry_surface=telemetry_surface
    )
    if evaluated.denied:
        return _deny(evaluated.reasons[0], member_ids=member_ids, denied=evaluated.denied)

    # Reduced per pair and combined identical-or-refuse across pairs, by the ONE
    # helper the interactive path uses. A pair nothing effective authorized is
    # refused loudly rather than treated as "no restriction" (the fail-open this
    # phase exists to close), and two different narrow slices across pairs are
    # refused rather than guessed between (``data_scope_conflict``).
    combined = combine_pair_scopes(
        db, organization_id=ctx.organization_id, per_pair=evaluated.per_pair
    )
    if not combined.allowed:
        authorization_denied(
            reason=combined.reason,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            dataset=entry.id,
        )
        return _deny(combined.reason, member_ids=member_ids)
    scope = combined.scope

    institution_grain = institution_grain_measures(cat, entry)
    if institution_grain and not scope.whole_institution:
        authorization_denied(
            reason=REASON_INSTITUTION_GRAIN,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            dataset=entry.id,
        )
        return _deny(REASON_INSTITUTION_GRAIN, member_ids=member_ids, denied=institution_grain)

    # The platform's ONE resolver, not a second reading of the same grant. It
    # also owns the two refusals a scope can carry: a grant wider than a single
    # ``IN`` list (``DataScopeUnservable``, a 422 the route reports verbatim) and
    # the invariant violation of an allowed decision with no slice.
    resolved = scope_resolver.resolve(
        db, scope, organization_id=ctx.organization_id, bank_id=bank.id
    )
    if not resolved.whole_institution and not resolved.branch_codes:
        # The ONE place the feed departs from the interactive surfaces, and the
        # reason is that nobody is watching. ``data_scope`` serves this case zero
        # rows under a filter that matches nothing, which is right for a person
        # looking at an empty grid — they can see it and ask. An unattended report
        # server cannot tell an empty stream from "nothing new since last time",
        # so a credential whose grant covers no branch this institution has ever
        # ingested is REFUSED, loudly, every pull, until someone fixes the grant.
        authorization_denied(
            reason=REASON_SCOPE_MATCHES_NO_BRANCH,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            surface=telemetry_surface,
            dataset=entry.id,
        )
        return _deny(REASON_SCOPE_MATCHES_NO_BRANCH, member_ids=member_ids)

    return FeedAuthorization(
        allowed=True,
        reason=REASON_ALLOWED,
        member_ids=member_ids,
        denied_members=(),
        matching_binding_ids=evaluated.matched,
        data_scope=scope,
        injected=resolved.filters,
        resolved=resolved,
    )


__all__ = [
    "REASON_INSTITUTION_GRAIN",
    "REASON_MACHINE_REQUIRED",
    "REASON_NO_DATA_SCOPE",
    "REASON_SCOPE_MATCHES_NO_BRANCH",
    "SURFACE_FEED",
    "FeedAuthorization",
    "authorize_feed",
    "machine_credential",
]
