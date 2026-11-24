# pyright: standard
"""The practice catalogue exported from AequorOS: documents, schemas, rules, publication."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from jsonschema import Draft202012Validator
from sqlalchemy import select

from app.db.models import ReturnDefinition
from app.filing.validation import codes_of, labels_of, lines_of
from app.returns.catalogue_loader import build_schema, documents, publish_catalogue
from app.returns.rules import PresentRule, RuleContext, SumRule, evaluate
from app.returns.service import check_draft, version_for_date, versions_of
from tests.harness import Harness


def _ctx(payload: Mapping[str, object]) -> RuleContext:
    return RuleContext(
        lines=lines_of(payload),  # type: ignore[arg-type]
        labels=labels_of(payload),  # type: ignore[arg-type]
        unit_label="",
        other_lines=lambda _code: None,
        previous_lines=None,
        previous_label=None,
        reporting_label="30 Jun 2026",
        codes=codes_of(payload),  # type: ignore[arg-type]
    )


def test_every_document_builds_a_valid_schema_and_rule_set() -> None:
    docs = documents()
    assert docs, "the catalogue exported from AequorOS is missing"
    for doc in docs:
        schema = build_schema(doc)
        Draft202012Validator.check_schema(schema)
        check_draft(schema, doc.rules)


def test_decimal_sum_with_subtraction_and_blank_cells() -> None:
    rule = SumRule(
        id="X-ARI-0001",
        severity="error",
        kind="sum",
        target="t",
        terms=["a", "b"],
        subtract=["c"],
        tolerance="0.015",
        description="t follows the template formula =a+b-c",
    )
    ok = {"lines": {"t": "10.5", "a": "7.25", "b": None, "c": "-3.24"}}
    assert evaluate([rule], _ctx(ok)).passed == 1
    off = {"lines": {"t": "10.6", "a": "7.25", "b": None, "c": "-3.24"}}
    result = evaluate([rule], _ctx(off))
    assert result.failed == 1
    assert "follows the template formula" in result.issues[0].message
    text = {"lines": {"t": "10.5", "a": "seven", "b": None, "c": "-3.24"}}
    assert "is not a number" in evaluate([rule], _ctx(text)).issues[0].message


def test_itemised_section_must_be_reported() -> None:
    rule = PresentRule(
        id="X-STR-001",
        severity="error",
        kind="present",
        section="cet1",
        description="Common Equity Tier 1 is reported",
    )
    filed = {"lines": {"cet1:1.1": "100.5"}, "labels": {"cet1:1.1": "Share capital"}}
    assert evaluate([rule], _ctx(filed)).passed == 1
    missing = {"lines": {"tier2:1.1": "5"}, "labels": {"tier2:1.1": "Subordinated debt"}}
    assert evaluate([rule], _ctx(missing)).failed == 1


def test_itemised_payload_matches_its_schema() -> None:
    doc = next(d for d in documents() if d.code == "CAR-RWA")
    validator = Draft202012Validator(build_schema(doc))
    payload = {
        "currency": "GHS",
        "lines": {
            "cet1:1.1": "35172763.04",
            "credit_rwa:3.1": "120.5",
            "credit_rwa:3.1:balance": "241",
            "capital_ratios:car": "18.25",
            "capital_ratios:car:passed": "true",
            "headline_comparative:total_rwa_ghs": "1",
        },
        "labels": {
            "cet1:1.1": "Retained earnings",
            "credit_rwa:3.1": "Claims on corporates",
            "capital_ratios:car": "Capital adequacy ratio",
            "headline_comparative:total_rwa_ghs": "Total RWA",
        },
    }
    assert list(validator.iter_errors(payload)) == []
    payload["lines"]["cet1:1.1"] = "35,172,763.04"
    assert [e.validator for e in validator.iter_errors(payload)] == ["pattern"]
    evaluation = evaluate(doc.rules, _ctx(payload))
    assert evaluation.failed == 0


def test_publish_replaces_the_stand_ins_and_covers_every_reporting_date(harness: Harness) -> None:
    from datetime import date

    from tests.synthetic_registry import bootstrap_registry

    with harness.ctx.db.session() as session:
        bootstrap_registry(session, harness.ctx, harness.clock.now(), codes=["CAR-RWA"])
    with harness.ctx.db.session() as session:
        done = publish_catalogue(session, harness.ctx)
    assert "CAR-RWA 2026.2" in done
    with harness.ctx.db.session() as session:
        row = session.scalars(
            select(ReturnDefinition).where(ReturnDefinition.code == "CAR-RWA")
        ).one()
        statuses = {v.version: v.status for v in versions_of(session, row.id)}
        assert statuses == {"2026.1": "retired", "2026.2": "published"}
        for day in (date(2025, 6, 30), date(2026, 6, 30), date(2026, 10, 31)):
            version = version_for_date(session, row.id, day)
            assert version is not None
            assert version.version == "2026.2"
    with harness.ctx.db.session() as session:
        assert publish_catalogue(session, harness.ctx) == []


def test_figures_are_exact_decimals() -> None:
    assert lines_of({"lines": {"a": "0.1", "b": "0.2", "c": None, "d": 3, "e": "x"}}) == {
        "a": Decimal("0.1"),
        "b": Decimal("0.2"),
        "c": Decimal(0),
        "d": Decimal(3),
    }


def test_the_seed_publishes_only_the_aligned_definitions(harness: Harness) -> None:
    from app.seed.registry import bootstrap_registry

    with harness.ctx.db.session() as session:
        published = bootstrap_registry(session, harness.ctx, harness.clock.now())
    assert len(published) == len(documents())
    with harness.ctx.db.session() as session:
        rows = session.scalars(select(ReturnDefinition)).all()
        assert {row.code for row in rows} == {doc.code for doc in documents()}
        for row in rows:
            versions = versions_of(session, row.id)
            assert [v.version for v in versions] == ["2026.2"]
            assert versions[0].schema.get("x-srg-practice") is True
