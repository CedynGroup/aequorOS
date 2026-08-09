"""Curve object model: registry codes, validation and value-based digests."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.curves.conventions import DayCount
from app.domain.curves.objects import (
    SUPPORTED_CURVE_CODES,
    CurveBuildResult,
    CurveDefinition,
    CurveNodes,
    CurveObjectError,
    canonical_input_digest,
    is_supported_curve_code,
)


def _definition(code: str = "AEQ.GHS.SOV.ZERO") -> CurveDefinition:
    return CurveDefinition(
        curve_code=code,
        curve_kind="zero",
        interpolation="monotone_convex",
        day_count=DayCount.ACT_364,
        extrapolation="flat_forward",
        instrument_selection=("min_trade_count", "max_staleness_days"),
    )


class TestRegistry:
    def test_the_five_spec_codes_are_registered(self) -> None:
        assert set(SUPPORTED_CURVE_CODES) == {
            "AEQ.GHS.SOV.ZERO",
            "AEQ.GHS.SOV.FWD",
            "AEQ.GHS.OIS",
            "AEQ.GHS.FTP.<BANKID>",
            "AEQ.GHS.CORP",
        }

    @pytest.mark.parametrize(
        "code", ["AEQ.GHS.SOV.ZERO", "AEQ.GHS.SOV.FWD", "AEQ.GHS.OIS", "AEQ.GHS.CORP"]
    )
    def test_fixed_codes_supported(self, code: str) -> None:
        assert is_supported_curve_code(code)

    def test_ftp_instantiation_supported_but_template_is_not(self) -> None:
        assert is_supported_curve_code("AEQ.GHS.FTP.BK-SAMP0001")
        # The raw template placeholder is a registry key, not a valid concrete code.
        assert not is_supported_curve_code("AEQ.GHS.FTP.<BANKID>")

    def test_unknown_codes_rejected(self) -> None:
        assert not is_supported_curve_code("AEQ.NGN.SOV.ZERO")
        assert not is_supported_curve_code("BVAL.GHS.SOV")


class TestCurveDefinition:
    def test_unknown_code_raises(self) -> None:
        with pytest.raises(CurveObjectError):
            _definition("AEQ.GHS.MAGIC")

    def test_frozen(self) -> None:
        definition = _definition()
        with pytest.raises(AttributeError):
            definition.interpolation = "pchip"  # type: ignore[misc]

    def test_payload_is_json_safe(self) -> None:
        payload = _definition().as_payload()
        assert payload["day_count"] == "ACT/364"
        assert payload["instrument_selection"] == ["min_trade_count", "max_staleness_days"]
        canonical_input_digest({"definition": payload})  # must not raise


class TestCurveNodes:
    def test_validation(self) -> None:
        with pytest.raises(CurveObjectError):
            CurveNodes(tenor_years=(1.0, 2.0), values=(0.1,))
        with pytest.raises(CurveObjectError):
            CurveNodes(tenor_years=(), values=())
        with pytest.raises(CurveObjectError):
            CurveNodes(tenor_years=(2.0, 1.0), values=(0.1, 0.2))
        with pytest.raises(CurveObjectError):
            CurveNodes(
                tenor_years=(1.0, 2.0), values=(0.1, 0.2), dates=(date(2027, 8, 3),)
            )

    def test_payload_includes_dates_when_present(self) -> None:
        nodes = CurveNodes(
            tenor_years=(1.0,), values=(0.12,), dates=(date(2027, 8, 3),)
        )
        assert nodes.as_payload()["dates"] == ["2027-08-03"]


class TestCanonicalDigest:
    def test_deterministic(self) -> None:
        payload = {"bills": [{"maturity": "2026-11-02", "discount": 0.0568}], "mpr": 0.08}
        assert canonical_input_digest(payload) == canonical_input_digest(payload)

    def test_key_order_insensitive(self) -> None:
        a = {"alpha": 1, "beta": {"x": 2.5, "y": 3.5}}
        b = {"beta": {"y": 3.5, "x": 2.5}, "alpha": 1}
        assert canonical_input_digest(a) == canonical_input_digest(b)

    def test_value_sensitive(self) -> None:
        a = {"discount": 0.056800}
        b = {"discount": 0.056801}
        assert canonical_input_digest(a) != canonical_input_digest(b)

    def test_non_finite_rejected(self) -> None:
        with pytest.raises(ValueError, match="[Nn]a[Nn]|float"):
            canonical_input_digest({"bad": float("nan")})


class TestCurveBuildResult:
    def test_identical_inputs_identical_results(self) -> None:
        nodes = CurveNodes(tenor_years=(0.25, 1.0), values=(0.0572, 0.1221))
        raw = {"bills": [0.0568, 0.114904], "params": {"policy": "volume_weight"}}
        first = CurveBuildResult.create(_definition(), nodes, None, raw)
        second = CurveBuildResult.create(_definition(), nodes, None, raw)
        assert first == second
        assert first.input_digest == second.input_digest
        assert len(first.input_digest) == 64  # sha256 hex

    def test_digest_covers_definition(self) -> None:
        nodes = CurveNodes(tenor_years=(0.25,), values=(0.0572,))
        raw = {"bills": [0.0568]}
        zero_def = _definition()
        ois_def = CurveDefinition(
            curve_code="AEQ.GHS.OIS",
            curve_kind="discount",
            interpolation="log_linear_df",
            day_count=DayCount.ACT_365F,
            extrapolation="flat_forward",
        )
        assert (
            CurveBuildResult.create(zero_def, nodes, None, raw).input_digest
            != CurveBuildResult.create(ois_def, nodes, None, raw).input_digest
        )

    def test_digest_covers_inputs_not_outputs(self) -> None:
        raw = {"bills": [0.0568]}
        nodes_a = CurveNodes(tenor_years=(0.25,), values=(0.0572,))
        nodes_b = CurveNodes(tenor_years=(0.25,), values=(0.0999,))
        digest_a = CurveBuildResult.create(_definition(), nodes_a, None, raw).input_digest
        digest_b = CurveBuildResult.create(_definition(), nodes_b, None, raw).input_digest
        assert digest_a == digest_b  # same inputs => same digest, whatever came out
