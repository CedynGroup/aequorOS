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
from app.services.institution_types import seed_rows as institution_type_seed_rows
from app.services.regulatory_parameters import seed_rows as regulatory_parameter_seed_rows

#: Ghana only. The production registry also carries NG/KE/ZA, but every fixture
#: bank is Ghanaian and a hermetic fixture should contain what it uses.
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


def seed_global_reference_data(session: Session) -> None:
    """Seed every global registry a ``create_all`` database is missing."""
    if session.get(Jurisdiction, GHANA["code"]) is None:
        session.add(Jurisdiction(**GHANA))
    for row in institution_type_seed_rows():
        if session.get(InstitutionType, row["type_code"]) is None:
            session.add(InstitutionType(**row))
    if session.query(RegulatoryParameter).first() is None:
        session.add_all(RegulatoryParameter(**row) for row in regulatory_parameter_seed_rows())
    session.flush()
