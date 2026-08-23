"""Calculation provenance formalised over the EXISTING RegulatoryRun (P4/P5)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.domain.authority.provenance import (
    REQUIRED_PROVENANCE_FIELDS,
    CalculationProvenance,
    ProvenanceIncomplete,
    RunLike,
    parameter_digest,
)
from app.models.regulatory_run import RegulatoryRun
from app.services.regulatory_reporting.generation import _source_run_entry

BANK_ID = "BK-SAMP0001"
ORG_ID = "OR-DEM00001"


def _run(**overrides: object) -> RegulatoryRun:
    """A real ``RegulatoryRun`` ORM object (unpersisted), shaped like the one
    ``regulatory_capital._persist_success`` writes."""
    period_id = uuid4()
    actor = uuid4()
    defaults: dict[str, object] = {
        "id": uuid4(),
        "organization_id": ORG_ID,
        "bank_id": BANK_ID,
        "reporting_period_id": period_id,
        "module": "capital",
        "scenario_code": "baseline",
        "status": "succeeded",
        "engine_version": "regulatory-capital-v1.0.0",
        "input_schema_version": "bank-facts-v2",
        "output_schema_version": "capital-metrics-v1",
        "input_hash": "a" * 64,
        "inputs": {
            "schema_version": "bank-facts-v2",
            "module": "capital",
            "scenario_code": "baseline",
            "bank_id": BANK_ID,
            "currency": "GHS",
            "jurisdiction_code": "GH",
            "as_of_date": "2026-06-30",
            "facts": [{"fact_group": "capital", "category": "cet1", "amount": "100"}],
            "parameters": {
                "risk_weights_pct": {"sovereign": "0", "corporate": "100"},
                "thresholds_pct": {"car_min": "13"},
            },
            "shocks": {"credit_pd_multiplier": "1.5"},
        },
        "metrics": {"car_pct": "18.4", "total_rwa_ghs": "1000"},
        "started_at": datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
        "completed_at": datetime(2026, 6, 30, 9, 0, 12, tzinfo=UTC),
        "created_by": actor,
    }
    defaults.update(overrides)
    return RegulatoryRun(**defaults)


def test_regulatory_run_satisfies_the_runlike_interface() -> None:
    """P5: the interface is formalised over what exists, not a replacement."""
    assert isinstance(_run(), RunLike)


def test_provenance_captures_every_required_field_for_a_real_run() -> None:
    run = _run()
    prov = CalculationProvenance.from_run(run)

    assert prov.run_id == str(run.id)
    assert prov.organization_id == ORG_ID
    assert prov.bank_id == BANK_ID
    assert prov.reporting_period_id == str(run.reporting_period_id)
    assert prov.module == "capital"
    assert prov.input_hash == run.input_hash
    assert prov.input_schema_version == "bank-facts-v2"
    assert prov.output_schema_version == "capital-metrics-v1"
    assert prov.engine_version == "regulatory-capital-v1.0.0"
    assert prov.scenario_code == "baseline"
    assert prov.computed_at == run.completed_at
    assert prov.actor_id == str(run.created_by)
    assert prov.parameter_digest == parameter_digest(run.inputs["parameters"])

    assert prov.missing_fields() == ()
    assert prov.is_complete is True
    assert prov.is_filable is True


def test_to_dict_covers_every_required_provenance_field() -> None:
    payload = CalculationProvenance.from_run(_run()).to_dict()
    for name in REQUIRED_PROVENANCE_FIELDS:
        assert name in payload, f"provenance dict is missing {name}"
        assert payload[name] not in (None, ""), f"provenance field {name} is empty"
    assert payload["jurisdiction_code"] == "GH"
    assert payload["base_currency"] == "GHS"
    assert payload["as_of_date"] == "2026-06-30"
    assert payload["shocks"] == {"credit_pd_multiplier": "1.5"}


def test_source_run_entry_matches_the_existing_package_wire_shape() -> None:
    """Byte-compatible with regulatory_reporting.generation._source_run_entry."""
    run = _run()
    assert CalculationProvenance.from_run(run).source_run_entry() == _source_run_entry(run)


def test_incomplete_provenance_is_detected_and_raises() -> None:
    prov = CalculationProvenance.from_run(_run(input_hash="", engine_version="   "))
    assert set(prov.missing_fields()) == {"input_hash", "engine_version"}
    assert prov.is_complete is False
    assert prov.is_filable is False
    with pytest.raises(ProvenanceIncomplete) as excinfo:
        prov.require_complete()
    assert excinfo.value.missing == prov.missing_fields()


def test_a_failed_run_is_complete_but_never_filable() -> None:
    prov = CalculationProvenance.from_run(_run(status="failed"))
    assert prov.is_complete is True
    assert prov.is_filable is False


def test_missing_completed_at_falls_back_to_started_at() -> None:
    prov = CalculationProvenance.from_run(_run(completed_at=None))
    assert prov.computed_at == datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
    assert prov.is_complete is True


def test_parameter_digest_is_value_based_and_order_independent() -> None:
    a = {"thresholds_pct": {"car_min": "13"}, "risk_weights_pct": {"sovereign": "0"}}
    b = {"risk_weights_pct": {"sovereign": "0"}, "thresholds_pct": {"car_min": "13"}}
    assert parameter_digest(a) == parameter_digest(b)
    assert parameter_digest(a) != parameter_digest({"thresholds_pct": {"car_min": "10"}})
    assert parameter_digest(None) == parameter_digest({})


def test_parameter_change_changes_the_provenance_digest() -> None:
    baseline = CalculationProvenance.from_run(_run())
    tightened_inputs = dict(_run().inputs)
    tightened_inputs["parameters"] = {"thresholds_pct": {"car_min": "14"}}
    tightened = CalculationProvenance.from_run(_run(inputs=tightened_inputs))
    assert baseline.parameter_digest != tightened.parameter_digest
    assert baseline.digest() != tightened.digest()


def test_provenance_is_immutable() -> None:
    prov = CalculationProvenance.from_run(_run())
    with pytest.raises((AttributeError, TypeError)):
        prov.input_hash = "b" * 64  # type: ignore[misc]


def test_run_without_parameters_still_yields_a_stable_digest() -> None:
    """Modules such as reverse_stress carry no ``parameters`` snapshot block."""
    prov = CalculationProvenance.from_run(
        _run(
            module="reverse_stress",
            engine_version="reverse-stress-v1.0.0",
            input_schema_version="reverse-stress-input-v1",
            output_schema_version="reverse-stress-frontier-v1",
            scenario_code="frontier",
            inputs={"schema_version": "reverse-stress-input-v1"},
        )
    )
    assert prov.parameter_digest == parameter_digest({})
    assert prov.is_complete is True
    assert prov.jurisdiction_code is None


def test_domain_authority_imports_no_database_layer() -> None:
    """``app/domain`` must stay pure: no SQLAlchemy / FastAPI / service imports."""
    package = Path(__file__).resolve().parents[3] / "app" / "domain" / "authority"
    forbidden = ("sqlalchemy", "fastapi", "from app.models", "from app.services", "from app.db")
    for path in sorted(package.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path.name} must not import {needle}"


def _uuid_check(value: object) -> None:  # pragma: no cover - typing aid
    assert isinstance(value, UUID)
