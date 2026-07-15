from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.ingestion.contracts import (
    CanonicalRecords,
    CounterpartyData,
    GlAccountData,
    PositionData,
)
from app.domain.ingestion.validation import (
    RuleConfig,
    ValidationConfig,
    ValidationContext,
    build_validation_report,
    default_validation_config,
    run_validation,
)

AS_OF = date(2026, 6, 30)


def make_position(reference: str = "LN-0001", **overrides: object) -> PositionData:
    values: dict[str, object] = {
        "source_reference": reference,
        "source_locator": f"wb.xlsx#Loans!{reference}",
        "position_type": "LOAN",
        "currency": "GHS",
        "balance": Decimal("1000"),
        "interest_rate": Decimal("0.245"),
        "contractual_maturity": date(2031, 3, 15),
    }
    values.update(overrides)
    return PositionData.model_validate(values)


def records_of(*positions: PositionData, **groups: list) -> CanonicalRecords:
    return CanonicalRecords(positions=list(positions), **groups)


def config_with(*rules: RuleConfig) -> ValidationConfig:
    return ValidationConfig(rules=list(rules))


def context(prior: dict[str, Decimal] | None = None) -> ValidationContext:
    return ValidationContext(as_of_date=AS_OF, prior_balances=prior)


class TestIndividualRules:
    def test_clean_batch_produces_no_findings(self) -> None:
        outcome = run_validation(
            records_of(make_position()), default_validation_config(), context()
        )
        assert outcome.findings == []
        assert outcome.overall_status == "accepted"

    def test_duplicate_source_references_flagged(self) -> None:
        rule = RuleConfig(name="structural_duplicate_source_references", severity="ERROR")
        outcome = run_validation(
            records_of(make_position("LN-1"), make_position("LN-1")),
            config_with(rule),
            context(),
        )
        (finding,) = outcome.findings
        assert finding.rule == rule.name
        assert "2 times" in finding.detail

    def test_unknown_counterparty_warns_by_default(self) -> None:
        rule = RuleConfig(name="structural_unknown_counterparty", severity="WARNING")
        known = CounterpartyData(
            source_reference="C-001",
            source_locator="wb#Customers!R2",
            name="Kojo Mensah",
            counterparty_type="RETAIL_INDIVIDUAL",
        )
        outcome = run_validation(
            records_of(make_position(counterparty_reference="C-404"), counterparties=[known]),
            config_with(rule),
            context(),
        )
        (finding,) = outcome.findings
        assert finding.severity == "WARNING"
        assert "'C-404'" in finding.detail

    def test_previously_ingested_counterparty_resolves(self) -> None:
        rule = RuleConfig(name="structural_unknown_counterparty", severity="WARNING")
        outcome = run_validation(
            records_of(make_position(counterparty_reference="C-EARLIER")),
            config_with(rule),
            ValidationContext(as_of_date=AS_OF, known_counterparties=frozenset({"C-EARLIER"})),
        )
        assert outcome.findings == []

    def test_dangling_product_reference_is_an_error(self) -> None:
        rule = RuleConfig(name="structural_unresolved_references", severity="ERROR")
        outcome = run_validation(
            records_of(make_position(product_code="LN.GHOST")),
            config_with(rule),
            ValidationContext(as_of_date=AS_OF, known_products=frozenset({"LN.REAL"})),
        )
        (finding,) = outcome.findings
        assert "product 'LN.GHOST'" in finding.detail

    def test_rate_bounds_use_configured_limits(self) -> None:
        rule = RuleConfig(
            name="position_rate_bounds",
            severity="ERROR",
            params={"minimum": "0", "maximum": "0.5"},
        )
        outcome = run_validation(
            records_of(
                make_position(interest_rate="0.45"), make_position("LN-2", interest_rate="0.75")
            ),
            config_with(rule),
            context(),
        )
        (finding,) = outcome.findings
        assert finding.source_reference == "LN-2"

    def test_unknown_currency_flagged(self) -> None:
        rule = RuleConfig(name="currency_iso_4217", severity="ERROR")
        outcome = run_validation(
            records_of(make_position(currency="GHC")),  # pre-2007 cedi code
            config_with(rule),
            context(),
        )
        (finding,) = outcome.findings
        assert "'GHC'" in finding.detail

    def test_matured_position_warned(self) -> None:
        rule = RuleConfig(name="maturity_not_before_as_of", severity="WARNING")
        outcome = run_validation(
            records_of(make_position(contractual_maturity=date(2024, 1, 1))),
            config_with(rule),
            context(),
        )
        (finding,) = outcome.findings
        assert finding.severity == "WARNING"

    def test_unusual_balance_change_needs_prior_state(self) -> None:
        rule = RuleConfig(
            name="unusual_balance_change", severity="WARNING", params={"threshold": "0.4"}
        )
        moved = records_of(make_position(balance="200"))
        assert run_validation(moved, config_with(rule), context()).findings == []
        outcome = run_validation(
            moved, config_with(rule), context(prior={"LN-0001": Decimal("100")})
        )
        (finding,) = outcome.findings
        assert "100" in finding.detail and "200" in finding.detail

    def test_unknown_rule_name_is_reported_not_fatal(self) -> None:
        outcome = run_validation(
            records_of(make_position()),
            config_with(RuleConfig(name="rule_from_the_future", severity="ERROR")),
            context(),
        )
        (finding,) = outcome.findings
        assert "Unknown validation rule" in finding.detail
        assert outcome.overall_status == "accepted_with_warnings"


class TestReconciliation:
    def rule(self, tolerance: str = "0.1") -> RuleConfig:
        return RuleConfig(
            name="gl_subledger_reconciliation",
            severity="BLOCKER",
            params={"tolerance_percent": tolerance},
        )

    def gl(self, code: str, balance: str) -> GlAccountData:
        return GlAccountData(
            source_reference=code,
            source_locator=f"wb#GL!{code}",
            account_code=code,
            name=f"Account {code}",
            account_class="ASSET",
            balance=Decimal(balance),
        )

    def test_within_tolerance_reports_but_does_not_block(self) -> None:
        outcome = run_validation(
            records_of(
                make_position(balance="1000.50", gl_account_code="1000"),
                gl_accounts=[self.gl("1000", "1000.00")],
            ),
            config_with(self.rule(tolerance="0.1")),
            context(),
        )
        assert outcome.findings == []
        account = outcome.reconciliation["gl_vs_subledger"]["1000"]
        assert account["within_tolerance"] is True
        assert account["difference"] == "0.50"

    def test_break_beyond_tolerance_blocks(self) -> None:
        outcome = run_validation(
            records_of(
                make_position(balance="1100", gl_account_code="1000"),
                gl_accounts=[self.gl("1000", "1000")],
            ),
            config_with(self.rule()),
            context(),
        )
        (finding,) = outcome.findings
        assert finding.severity == "BLOCKER"
        assert outcome.overall_status == "rejected"


class TestGatingSemantics:
    def test_error_excludes_the_record_but_not_the_batch(self) -> None:
        outcome = run_validation(
            records_of(
                make_position(interest_rate="0.245"), make_position("LN-BAD", interest_rate="1.75")
            ),
            config_with(RuleConfig(name="position_rate_bounds", severity="ERROR")),
            context(),
        )
        assert outcome.overall_status == "accepted_with_warnings"
        assert outcome.record_statuses[("position", "LN-BAD")] == "error"
        assert outcome.record_statuses[("position", "LN-0001")] == "accepted"

    def test_blocker_rejects_every_record_in_the_batch(self) -> None:
        outcome = run_validation(
            records_of(
                make_position(balance="9999", gl_account_code="1000"),
                make_position("LN-OK"),
                gl_accounts=[
                    GlAccountData(
                        source_reference="1000",
                        source_locator="wb#GL!1000",
                        account_code="1000",
                        name="Loans control",
                        account_class="ASSET",
                        balance=Decimal("100"),
                    )
                ],
            ),
            config_with(
                RuleConfig(name="gl_subledger_reconciliation", severity="BLOCKER"),
            ),
            context(),
        )
        assert outcome.overall_status == "rejected"
        assert set(outcome.record_statuses.values()) == {"blocked"}

    def test_severity_is_configuration_not_code(self) -> None:
        downgraded = run_validation(
            records_of(make_position(interest_rate="1.75")),
            config_with(RuleConfig(name="position_rate_bounds", severity="WARNING")),
            context(),
        )
        assert downgraded.record_statuses[("position", "LN-0001")] == "warning"
        assert downgraded.overall_status == "accepted_with_warnings"

    def test_disabled_rule_does_not_run(self) -> None:
        outcome = run_validation(
            records_of(make_position(interest_rate="1.75")),
            config_with(RuleConfig(name="position_rate_bounds", severity="ERROR", enabled=False)),
            context(),
        )
        assert outcome.findings == []


class TestReport:
    def test_report_shape_matches_the_operator_contract(self) -> None:
        outcome = run_validation(
            records_of(make_position(), make_position("LN-BAD", interest_rate="1.75")),
            default_validation_config(),
            context(),
        )
        report = build_validation_report(outcome, records_extracted=3, records_translated=2)
        assert report["summary"] == {
            "records_extracted": 3,
            "records_translated": 2,
            "records_accepted": 1,
            "records_warning": 0,
            "records_error": 1,
            "records_blocked": 0,
            "reference_rows": {},
            "overall_status": "ACCEPTED_WITH_WARNINGS",
        }
        (failure,) = report["failures"]
        assert failure["rule"] == "position_rate_bounds"
        assert failure["source_reference"] == "LN-BAD"
        assert failure["severity"] == "ERROR"
