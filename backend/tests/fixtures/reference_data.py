"""Global (non-tenant) reference data for a schema built with ``create_all``.

In a deployed database the migrations own this data: the jurisdictions registry
(``202607230017`` + ``202608110053``), the institution-type registry
(``202608190018`` as amended by ``202608210026``) and the regulatory-parameter
control plane (``202608200025`` + ``202608220032`` + ``202608220034``). Nothing
hermetic runs migrations — the pytest suite and the Playwright e2e stack both
build their disposable database with ``Base.metadata.create_all``, because RLS
and seeds living only in migrations is what makes the hermetic path hermetic —
so every one of those registries has to be seeded explicitly here, from the SAME
catalogues the migrations read (``institution_types.seed_rows`` /
``regulatory_parameters.seed_rows``) so the fixture and the deployment can never
drift.

**One function on purpose.** This used to be two hand-written copies, and the
copy in ``scripts/e2e_bootstrap.py`` seeded neither the institution-type registry
nor the parameter control plane. That was invisible until institution-type
resolution became fail-closed (P0-12): from then on the whole e2e stack died in
global setup with a 409 naming a seed migration that a ``create_all`` database
never runs. Adding a global registry is now a one-line change here and both
callers get it.

Idempotent, so a caller may run it against a database that already has some of
the rows.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import InstitutionType, Jurisdiction, RegulatoryParameter
from app.services.icaap.parameters import (
    jurisdiction_seed_rows as icaap_jurisdiction_parameter_seed_rows,
)
from app.services.icaap.parameters import seed_rows as icaap_parameter_seed_rows
from app.services.institution_types import seed_rows as institution_type_seed_rows
from app.services.regulatory_parameters import filing_seed_rows as icaap_filing_parameter_seed_rows
from app.services.regulatory_parameters import p2_seed_rows as icaap_p2_parameter_seed_rows
from app.services.regulatory_parameters import p5_seed_rows as p5_parameter_seed_rows
from app.services.regulatory_parameters import seed_rows as regulatory_parameter_seed_rows

#: Ghana, Nigeria and Kenya — the three jurisdictions something in the suite now
#: uses. Ghana is every fixture bank's; NG and KE arrived with the CBN and CBK
#: ICAAP frameworks, whose cycles resolve a regulator name and a governed
#: parameter row per jurisdiction. ZA is in the production registry and nothing
#: here reads it, so a hermetic fixture still contains what it uses. Field for
#: field these mirror ``202607230017``'s ``SEED_ROWS``; a test holds them equal.
GHANA = {
    "code": "GH",
    "country_name": "Ghana",
    "currency_code": "GHS",
    "currency_name": "Ghana Cedi",
    "locale": "en-GH",
    "central_bank_name": "Bank of Ghana",
    "regulator_short": "BoG",
    "sovereign_rating_issuer": "GHANA_SOVEREIGN",
    "submission_portal": "ORASS",
    "timezone": "Africa/Accra",
}
NIGERIA = {
    "code": "NG",
    "country_name": "Nigeria",
    "currency_code": "NGN",
    "currency_name": "Nigerian Naira",
    "locale": "en-NG",
    "central_bank_name": "Central Bank of Nigeria",
    "regulator_short": "CBN",
    "sovereign_rating_issuer": None,
    "submission_portal": None,
    "timezone": "Africa/Lagos",
}
KENYA = {
    "code": "KE",
    "country_name": "Kenya",
    "currency_code": "KES",
    "currency_name": "Kenyan Shilling",
    "locale": "en-KE",
    "central_bank_name": "Central Bank of Kenya",
    "regulator_short": "CBK",
    "sovereign_rating_issuer": None,
    "submission_portal": None,
    "timezone": "Africa/Nairobi",
}
JURISDICTIONS = (GHANA, NIGERIA, KENYA)


def seed_global_reference_data(session: Session) -> None:
    """Seed every global registry a ``create_all`` database is missing."""
    for jurisdiction in JURISDICTIONS:
        if session.get(Jurisdiction, jurisdiction["code"]) is None:
            session.add(Jurisdiction(**jurisdiction))
    for row in institution_type_seed_rows():
        if session.get(InstitutionType, row["type_code"]) is None:
            session.add(InstitutionType(**row))
    if session.query(RegulatoryParameter).first() is None:
        session.add_all(RegulatoryParameter(**row) for row in regulatory_parameter_seed_rows())
    # The ICAAP codes are seeded by their own migrations (202609190055 for the
    # workspace, 202609190056 for the Pillar 2 engine, 202609190058 for the
    # filing plane, 202609200063 for Nigeria and Kenya) and so are added row by
    # row rather than under the "table is empty" guard above: a database built
    # before ICAAP shipped has parameters but not these. The five sets are
    # disjoint on (code, jurisdiction) — each row is seeded exactly once, and
    # tests/services/test_regulatory_parameters_icaap_p2.py,
    # tests/services/test_regulatory_parameters_p5.py and
    # tests/services/test_regulatory_parameters_icaap_jurisdictions.py pin that.
    for row in (
        *icaap_parameter_seed_rows(),
        *icaap_p2_parameter_seed_rows(),
        *icaap_filing_parameter_seed_rows(),
        *p5_parameter_seed_rows(),
        *icaap_jurisdiction_parameter_seed_rows(),
    ):
        existing = (
            session.query(RegulatoryParameter)
            .filter_by(
                param_code=row["param_code"],
                scope_type=row["scope_type"],
                scope_key=row["scope_key"],
                jurisdiction_code=row["jurisdiction_code"],
            )
            .first()
        )
        if existing is None:
            session.add(RegulatoryParameter(**row))
    session.flush()
