"""The one seam a return FAMILY may use to add rules to the generic plane.

The regulatory reporting plane is family-agnostic by construction: one package
table, one submission gate, one validation pipeline, one exporter. ICAAP P3 is
the first family that genuinely needs more — a filing whose package is minted by
freezing a workspace cycle, whose Board resolution is a submission precondition,
and whose document is rendered from a frozen narrative snapshot rather than from
a tabular template.

The wrong answer is ``if package.return_family == "icaap"`` scattered through
five services. This module is the right one: every generic service asks
:func:`for_family` once, and a family that declares no hooks changes nothing.
The dispatch is LAZY (``importlib`` at call time) because the hook
implementations live in the family's own service package, which imports the
generic plane — a module-level import would close the cycle.

Every hook is optional. :class:`FamilyHooks` supplies no-op defaults so a family
implements only what it actually changes, and a hook added here later cannot
break an existing implementation.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext
    from app.models import RegulatoryPackage

logger = logging.getLogger(__name__)


class FamilyHooks:
    """The complete family seam, with a no-op default for every hook.

    Subclassing is not required — any object carrying these attributes is
    accepted — but subclassing is what makes "I only override what I change"
    true, so the family modules do.
    """

    #: The family this instance serves, for the trace and for the registry pin.
    family: str = ""

    # --- submission ------------------------------------------------------
    def required_attachments(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> dict[str, int]:
        """Family-INTRINSIC document requirements as ``{kind: min_count}``.

        Intrinsic means "the framework asks for this", not "the bank's signing
        policy asks for this". The two are unioned by the caller and the
        intrinsic half is deliberately NOT relaxable by a signing policy:
        dropping a signature slot changes signatures, never which regulator
        documents accompany a filing (audit M11).
        """
        _ = (db, ctx, package)
        return {}

    def ensure_submittable(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: Any
    ) -> None:
        """Refuse a submission the family's own rules do not permit."""
        _ = (db, ctx, package, policy)

    def ensure_voidable(self, db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
        """Refuse voiding an attestation the family's own rules protect."""
        _ = (db, ctx, package)

    # --- validation ------------------------------------------------------
    def validation_findings(self, db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
        """Extra validation findings, in the generic ``{rule, severity, detail}``
        shape. These MAY be ``ERROR`` — unlike generation notes, which are
        downgraded — because a family rule is a rule about the filing."""
        _ = (db, package)
        return []

    # --- lifecycle -------------------------------------------------------
    def on_transition(
        self,
        db: Session,
        ctx: TenantContext,
        package: RegulatoryPackage,
        *,
        previous: str,
        new_status: str,
    ) -> None:
        """React to a package status transition (no commit)."""
        _ = (db, ctx, package, previous, new_status)

    def on_fully_certified(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> None:
        """React to the final required signature landing (no commit)."""
        _ = (db, ctx, package)

    def on_package_superseded(
        self, db: Session, ctx: TenantContext, package: RegulatoryPackage
    ) -> None:
        """React to a direct supersession by a regenerated version (no commit)."""
        _ = (db, ctx, package)

    # --- attestation ------------------------------------------------------
    def certification_statement(self, package: RegulatoryPackage, role: str) -> str | None:
        """The sentence this officer attests to, or ``None`` for the default.

        A family whose statements are part of the FROZEN document (the ICAAP
        framework's ``filing.attestation.statements`` block) answers from the
        snapshot, so a signature always carries the wording that was in force
        when the document was sealed rather than the deployment's current text.
        """
        _ = (package, role)
        return None

    # --- export ----------------------------------------------------------
    def export(
        self,
        db: Session,
        ctx: TenantContext,
        package: RegulatoryPackage,
        kind: str,
        bank: Any,
    ) -> tuple[bytes, str, str]:
        """Render one artifact: ``(payload, extension, file_stem)``.

        Raising :class:`NotImplementedError` means "this family renders through
        the generic template path", which is what every family but ICAAP does.
        """
        raise NotImplementedError(self.family or "family")


#: family -> the module that carries its ``HOOKS`` instance. Declared here so
#: the set of families with hooks is READABLE in one place; resolved lazily.
HOOK_MODULES: Final[dict[str, str]] = {
    "icaap": "app.services.icaap.filing_hooks",
}

#: Explicitly installed hooks, which take precedence over :data:`HOOK_MODULES`.
#: The seam exists so a test can drive the dispatch with a stub family without
#: creating a module, and so a future family can register from its own package.
_REGISTERED: dict[str, FamilyHooks] = {}

_MISSING_LOGGED: set[str] = set()


def register(family: str, hooks: FamilyHooks) -> None:
    """Install hooks for ``family`` (overrides :data:`HOOK_MODULES`)."""
    _REGISTERED[family] = hooks


def unregister(family: str) -> None:
    """Remove explicitly installed hooks for ``family`` (a no-op if absent)."""
    _REGISTERED.pop(family, None)


def for_family(family: str | None) -> FamilyHooks | None:
    """The hooks for ``family``, or ``None`` when it declares none.

    A declared module that does not import is reported once at WARNING and
    treated as "no hooks". That is not a fail-open: the only packages a hooked
    family can have are the ones its own freeze path mints, so a tree without
    the module has no package for the missing rules to have protected. Reported
    rather than silent, because a deployment that half-shipped a family should
    say so in its logs.
    """
    if not family:
        return None
    installed = _REGISTERED.get(family)
    if installed is not None:
        return installed
    module_path = HOOK_MODULES.get(family)
    if module_path is None:
        return None
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        if family not in _MISSING_LOGGED:
            _MISSING_LOGGED.add(family)
            logger.warning(
                "family_hooks.module_missing family=%s module=%s "
                "(the family's own rules are not enforced in this build)",
                family,
                module_path,
            )
        return None
    hooks = getattr(module, "HOOKS", None)
    if hooks is None:
        logger.warning("family_hooks.hooks_missing family=%s module=%s", family, module_path)
        return None
    return hooks


def for_package(package: RegulatoryPackage) -> FamilyHooks | None:
    """:func:`for_family` keyed on the package's own recorded family."""
    return for_family(package.return_family)


# --- the generic plane's call sites, as named helpers ------------------------
# Each of these is what a generic service calls, so the "is there a hook?" test
# lives here once instead of at six call sites.


def required_attachments(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> dict[str, int]:
    hooks = for_package(package)
    return {} if hooks is None else hooks.required_attachments(db, ctx, package)


def ensure_submittable(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, policy: Any
) -> None:
    hooks = for_package(package)
    if hooks is not None:
        hooks.ensure_submittable(db, ctx, package, policy)


def ensure_voidable(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
    hooks = for_package(package)
    if hooks is not None:
        hooks.ensure_voidable(db, ctx, package)


def validation_findings(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    hooks = for_package(package)
    return [] if hooks is None else hooks.validation_findings(db, package)


def on_transition(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    previous: str,
    new_status: str,
) -> None:
    hooks = for_package(package)
    if hooks is not None:
        hooks.on_transition(db, ctx, package, previous=previous, new_status=new_status)


def on_fully_certified(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
    hooks = for_package(package)
    if hooks is not None:
        hooks.on_fully_certified(db, ctx, package)


def on_package_superseded(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
    hooks = for_package(package)
    if hooks is not None:
        hooks.on_package_superseded(db, ctx, package)


__all__ = [
    "HOOK_MODULES",
    "FamilyHooks",
    "ensure_submittable",
    "ensure_voidable",
    "for_family",
    "for_package",
    "on_fully_certified",
    "on_package_superseded",
    "on_transition",
    "register",
    "required_attachments",
    "unregister",
    "validation_findings",
]
