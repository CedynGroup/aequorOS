"""Institution profile, pinned to the register-state digest.

The register has no as-of date and no sealed run, so the binding pins the
digest of the projected register rows — the same digest an attestation binds
to. If a related party or shareholding changes after the section is written,
the block goes stale and says so.
"""

from __future__ import annotations

from sqlalchemy import select

from app.domain.icaap.blocks import SourceProbe
from app.models import InstitutionProfile
from app.services.attestation import register_state
from app.services.attestation.digests import register_state_digest
from app.services.icaap import resolvers
from app.services.icaap.resolvers import Resolution, ResolveContext, Unavailable

_VERSION = "1"


def _digest(rc: ResolveContext) -> tuple[str, int]:
    rows = register_state.register_state_rows(rc.db, rc.access.ctx, rc.access.bank.id)
    return register_state_digest(rows), len(rows)


class InstitutionProfileResolver:
    block_type = "institution_profile"
    version = _VERSION

    def probe(self, rc: ResolveContext) -> SourceProbe:
        digest, _count = _digest(rc)
        return SourceProbe(current_key=f"register:{digest}")

    def resolve(self, rc: ResolveContext) -> Resolution:
        profile = rc.db.scalar(
            select(InstitutionProfile).where(
                InstitutionProfile.organization_id == rc.access.ctx.organization_id,
                InstitutionProfile.bank_id == rc.access.bank.id,
            )
        )
        if profile is None:
            raise Unavailable(
                "This institution has no profile register yet. Complete Settings > "
                "Institution profile, then link this figure again."
            )
        digest, count = _digest(rc)
        spec = rc.spec
        table = resolvers.TableBuilder("profile", "Institution profile")
        table.column("field", "Field", "text").column("value", "Value", "text")
        entries = (
            ("Licence type", profile.institution_type),
            ("Legal entity structure", profile.legal_entity_structure),
            ("Incorporated", profile.incorporation_date),
            ("Authorised", profile.authorisation_date),
            ("Parent country", profile.parent_country_code),
            ("Local ownership", profile.ownership_local_pct),
            ("Foreign ownership", profile.ownership_foreign_pct),
            ("Traded on an exchange", profile.traded_on_exchange),
            ("Exchange", profile.exchange_name),
        )
        for label, value in entries:
            table.row({"field": label, "value": value})
        facts = {
            "legal_entity_structure": resolvers.fact(
                spec, "legal_entity_structure", profile.legal_entity_structure
            ),
            "institution_type": resolvers.fact(spec, "institution_type", profile.institution_type),
            "parent_country_code": resolvers.fact(
                spec, "parent_country_code", profile.parent_country_code
            ),
            "ownership_local_pct": resolvers.fact(
                spec, "ownership_local_pct", profile.ownership_local_pct
            ),
            "ownership_foreign_pct": resolvers.fact(
                spec, "ownership_foreign_pct", profile.ownership_foreign_pct
            ),
        }
        body = resolvers.payload(
            title=spec.title,
            as_of=None,
            source_label="Institution profile register",
            currency=rc.currency,
            tables=[table.build()],
        )
        return Resolution(
            source_kind="register",
            source_ref={"digest": digest, "row_count": count},
            source_key=f"register:{digest}",
            source_as_of=None,
            payload=body,
            facts=facts,
        )


__all__ = ["InstitutionProfileResolver"]
