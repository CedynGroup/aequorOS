"""Nigeria and Kenya, end to end: the frameworks now resolve.

Both shipped as data in P5-E and were published-but-non-functional until
``202609200063``: resolution matches ``jurisdiction_code`` exactly and never
falls back to Ghana, so every governed read — the filing deadline, the review
intervals, the materiality matrix, Kenya's two horizons — answered
``missing_parameter`` and a cycle could not be opened at all. That is correct
behaviour for an unseeded code (D-024 §4) and useless to a bank.

This file proves the other half: with the rows seeded, a Nigerian and a Kenyan
institution open a cycle, get the regulator's own deadline, and read a checklist
whose sentences carry figures rather than ``{param:…}`` placeholders. Every one
of those figures is labelled pending confirmation, because the primary texts
have still not been read here (D-076) — a functional framework and a verified
one are different claims, and only the first is made.

The fixture bank is the canonical one with its jurisdiction changed, which is
the smallest change that exercises the real path: nothing in the ICAAP services
branches on country, so the jurisdiction column and the registry row are the
whole of the difference.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.core.config import get_settings
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework
from app.models import Bank
from app.schemas.icaap import IcaapCycleCreate
from app.services.icaap import cycles, parameters, readiness, risks, sections, serializers

FY = 2025
AS_OF = date(2025, 12, 31)
#: Both instruments are filed four months after a 31 December position, so both
#: land on the same day. The values are separate governed rows; the equality is
#: a coincidence of the two regulators' deadlines, not a shared row.
DUE = date(2026, 4, 30)
NG = ("cbn_srp_icaap", "2021.09", "NG")
KE = ("cbk_icaap_gn", "2016.11", "KE")
CASES = [NG, KE]
IDS = [f"{code}-{jurisdiction}" for code, _version, jurisdiction in CASES]

_PLACEHOLDER = re.compile(r"\{param:[a-z][a-z0-9_]{2,60}\}")


def _frameworks() -> dict[str, Framework]:
    return {framework.jurisdiction: framework for framework in registry.load_all()}


@pytest.fixture
def opt_in_frameworks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Publish Nigeria and Kenya for this test only.

    ``ICAAP_FRAMEWORKS_ENABLED`` defaults to Ghana alone and stays that way:
    turning a jurisdiction on is a deployment decision that D-076 gates on the
    primary texts being obtained and verified. Seeding the rows removes the
    technical blocker, not that one.
    """
    codes = ",".join(["bog_icaap", NG[0], KE[0]])
    monkeypatch.setenv("ICAAP_FRAMEWORKS_ENABLED", codes)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


#: A SIBLING institution per jurisdiction, never the canonical Ghanaian one.
#:
#: The first version of this file flipped ``jurisdiction_code`` on the shared
#: canonical bank and did not put it back. The fixture commits, so the change
#: outlived the test and surfaced elsewhere as ``- GH / + KE`` in files that
#: have nothing to do with either country — ordering-dependent, and with
#: ``pytest-randomly`` in play, intermittent and misattributed. A separate bank
#: also says the thing the test is about: these frameworks need a non-Ghanaian
#: institution, and the platform has to be able to hold one beside a Ghanaian
#: one in the same tenant.
#: jurisdiction -> (platform id, base currency, name, short name)
JURISDICTION_BANKS: dict[str, tuple[str, str, str, str]] = {
    "NG": ("BK-NGTEST01", "NGN", "Test Bank Nigeria Ltd", "Test NG"),
    "KE": ("BK-KETEST01", "KES", "Test Bank Kenya Ltd", "Test KE"),
}


def _access_for(session: Session, base: IcaapAccess, jurisdiction: str) -> IcaapAccess:
    """The canonical bank for Ghana; a sibling of its own for NG and KE."""
    if jurisdiction == "GH":
        bank = session.get(Bank, base.bank.id)
        assert bank is not None
        assert bank.jurisdiction_code == "GH", (
            "the canonical fixture bank is Ghanaian and must stay that way"
        )
        return IcaapAccess(ctx=base.ctx, bank=bank)
    bank_id, currency, name, short_name = JURISDICTION_BANKS[jurisdiction]
    bank = session.get(Bank, bank_id)
    if bank is None:
        bank = Bank(
            id=bank_id,
            organization_id=base.bank.organization_id,
            name=name,
            short_name=short_name,
            currency=currency,
            jurisdiction_code=jurisdiction,
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.flush()
    return IcaapAccess(ctx=base.ctx, bank=bank)


def _open_cycle(
    session: Session, access: IcaapAccess, code: str, version: str
) -> tuple[IcaapAccess, object]:
    payload = IcaapCycleCreate.model_validate(
        {
            "fiscal_year": FY,
            "cycle_kind": "annual",
            "basis": "solo",
            "framework_code": code,
            "framework_version": version,
            "reason": "First assessment under this instrument",
        }
    )
    return access, cycles.create_cycle(session, access, payload)


# --- every row resolves ----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "jurisdiction"),
    [
        (code, jurisdiction)
        for _framework_code, _version, jurisdiction in CASES
        for code in sorted(_frameworks()[jurisdiction].param_refs())
    ],
    ids=str,
)
def test_every_code_the_framework_quotes_resolves_for_its_jurisdiction(
    canonical_book: Session, access: IcaapAccess, code: str, jurisdiction: str
) -> None:
    """One assertion per seeded row, through the service's own door.

    ``parameters.resolve`` is the ICAAP plane's only route to a governed row, so
    a row that exists but is scoped wrong — wrong institution class, wrong
    status, superseded — fails here exactly as it would for a bank.
    """
    scoped = _access_for(canonical_book, access, jurisdiction)
    resolved = parameters.resolve(
        canonical_book, scoped.bank, code, purpose="test", as_of=AS_OF
    )
    assert resolved.jurisdiction_code == jurisdiction
    assert (resolved.value is None) != (resolved.value_json is None)
    # D-076: the primary texts have not been read, so nothing may read as final.
    assert resolved.confirmation_status == "pending"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_ghana_row_never_answers_for_another_regulator(
    canonical_book: Session,
    access: IcaapAccess,
    case: tuple[str, str, str],
) -> None:
    """The gap these rows closed, stated as the property that created it.

    Resolution has no jurisdiction fallback, which is why publishing the
    frameworks was not enough. It is also what stops Ghana's three-month
    deadline from quietly becoming Nigeria's, so it must stay true.
    """
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    resolved = parameters.resolve(
        canonical_book,
        scoped.bank,
        parameters.SUBMISSION_MONTHS,
        purpose="test",
        as_of=AS_OF,
    )
    ghana = parameters.resolve(
        canonical_book,
        _access_for(canonical_book, access, "GH").bank,
        parameters.SUBMISSION_MONTHS,
        purpose="test",
        as_of=AS_OF,
    )
    assert resolved.parameter_id != ghana.parameter_id
    assert resolved.value != ghana.value


# --- the framework loads end to end ----------------------------------------


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_framework_read_carries_the_regulators_deadline(
    canonical_book: Session,
    access: IcaapAccess,
    opt_in_frameworks: None,
    case: tuple[str, str, str],
) -> None:
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    read = cycles.get_framework(canonical_book, scoped, framework_code, version)
    assert read.jurisdiction == jurisdiction
    assert read.deadline.months_after_fy_end == 4
    assert read.deadline.months_param_code == parameters.SUBMISSION_MONTHS
    assert read.deadline.months_confirmation_status == "pending"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_cycle_opens_and_takes_its_due_date_from_the_framework(
    canonical_book: Session,
    access: IcaapAccess,
    opt_in_frameworks: None,
    case: tuple[str, str, str],
) -> None:
    """The refusal this used to be was ``missing_parameter`` on the deadline."""
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    _scoped, cycle = _open_cycle(canonical_book, scoped, framework_code, version)
    assert cycle.framework.code == framework_code  # type: ignore[attr-defined]
    assert cycle.framework.version == version  # type: ignore[attr-defined]
    assert cycle.due_date == DUE  # type: ignore[attr-defined]
    assert cycle.due_date_basis == "framework"  # type: ignore[attr-defined]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_checklist_sentence_renders_its_figure(
    canonical_book: Session,
    access: IcaapAccess,
    opt_in_frameworks: None,
    case: tuple[str, str, str],
) -> None:
    """No ``{param:…}`` survives into a sentence a bank reads, and each says pending.

    This is the whole point of the seeding: the placeholder is what a preparer
    would have seen if a row resolved to nothing, and the pending note is what
    keeps an unverified figure from reading as the regulator's settled word.
    """
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    _scoped, cycle = _open_cycle(canonical_book, scoped, framework_code, version)
    framework = _frameworks()[jurisdiction]
    quoted = 0
    for section in framework.sections:
        read = sections.get_section(canonical_book, scoped, cycle.id, section.key)  # type: ignore[attr-defined]
        for requirement in read.requirements:
            assert not _PLACEHOLDER.search(requirement.resolved_text), requirement.item.id
            if requirement.item.param_refs:
                quoted += 1
                assert "pending confirmation" in requirement.resolved_text.casefold(), (
                    requirement.item.id
                )
    assert quoted, f"{framework_code} quotes no figure in any sentence"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_risk_register_builds_its_materiality_matrix(
    canonical_book: Session,
    access: IcaapAccess,
    opt_in_frameworks: None,
    case: tuple[str, str, str],
) -> None:
    """The three matrix rows are structural, not quoted in any sentence.

    So they are unreachable from the checklist test above and have their own —
    the register is where a missing band or threshold would refuse.
    """
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    _scoped, cycle = _open_cycle(canonical_book, scoped, framework_code, version)
    register = risks.get_register(canonical_book, scoped, cycle.id)  # type: ignore[attr-defined]
    assert register.risks
    assert register.matrix.bands
    assert register.matrix.material_min_score == 10
    assert register.matrix.material_min_impact == 4
    codes = {entry.param_code for entry in register.matrix.parameters}
    assert set(risks.MATERIALITY_CODES) <= codes
    for entry in register.matrix.parameters:
        if entry.param_code in set(risks.MATERIALITY_CODES):
            # The platform's 5x5 default, which per the extraction record
            # neither regulator prescribes. The label has to reach the register
            # or a reader takes it for the supervisor's matrix.
            assert entry.representative is True, entry.param_code
            assert entry.confirmation_status == "pending"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_readiness_answers_instead_of_refusing(
    canonical_book: Session,
    access: IcaapAccess,
    opt_in_frameworks: None,
    case: tuple[str, str, str],
) -> None:
    framework_code, version, jurisdiction = case
    scoped = _access_for(canonical_book, access, jurisdiction)
    _scoped, cycle = _open_cycle(canonical_book, scoped, framework_code, version)
    report = readiness.get_readiness(canonical_book, scoped, cycle.id)  # type: ignore[attr-defined]
    assert report.deadline.due_date == DUE
    assert report.items


def test_the_pending_note_does_not_claim_a_stakeholder_was_asked() -> None:
    """Production copy, and it has to be true in every jurisdiction.

    It read "(figure pending stakeholder confirmation)". For Ghana that is a
    fair description of an exposure draft; for Nigeria and Kenya it is not —
    the outstanding step is reading the regulator's text, which nobody here has
    done (D-076), not hearing back from a stakeholder. A preparer who read the
    old note beside a CBN interval would conclude the opposite of the truth.
    The note now says only what holds for every pending row.
    """
    assert serializers.PENDING_FIGURE_NOTE == " (figure pending confirmation)"
    assert "stakeholder" not in serializers.PENDING_FIGURE_NOTE
