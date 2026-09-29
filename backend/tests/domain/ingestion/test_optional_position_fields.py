"""The four optional Data Engine fields: accepted, normalised, or refused with a
message an integration engineer can act on (``docs/bi.md`` §Phase 5).

The properties under test are the ones the design turns on:

* each field is accepted and normalised to ONE spelling;
* a malformed value is DROPPED rather than stored as free text, and the message
  names the key, the value sent and what is accepted;
* the position itself still lands — an optional descriptive field never deletes a
  facility from the balance sheet;
* a push with none of the four is byte-identical to one before the fields existed;
* absent is not zero, in either direction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.domain.ingestion.contracts import CanonicalRecords, PositionData
from app.domain.ingestion.optional_position_fields import (
    ACCOUNT_STATUS_ALIASES,
    ACCOUNT_STATUSES,
    CHANNEL_ALIASES,
    CHANNELS,
    OFFICER_ID_MAX_LENGTH,
    OPTIONAL_POSITION_ATTRIBUTE_KEYS,
    normalize_attributes,
    normalize_positions,
    stated_arrears_amount,
)
from app.domain.ingestion.validation import (
    RuleConfig,
    ValidationConfig,
    ValidationContext,
    default_validation_config,
    run_validation,
)

AS_OF = date(2026, 6, 30)


def position(reference: str = "LN-0001", **overrides: object) -> PositionData:
    values: dict[str, object] = {
        "source_reference": reference,
        "source_locator": f"source.json#position!{reference}",
        "position_type": "LOAN",
        "currency": "GHS",
        "balance": Decimal("1000"),
    }
    values.update(overrides)
    return PositionData.model_validate(values)


def normalized(**attributes: object) -> dict[str, object]:
    values, problems = normalize_attributes(attributes)
    assert problems == (), problems
    return values


def refusal(**attributes: object) -> str:
    values, problems = normalize_attributes(attributes)
    assert len(problems) == 1, problems
    assert values == {}, "a refused value must not be stored"
    return problems[0][1]


class TestEachFieldIsAcceptedAndNormalised:
    def test_all_four_together(self) -> None:
        assert normalized(
            officer_id="RM-014",
            channel="branch",
            account_status="active",
            arrears_amount="1250.50",
        ) == {
            "officer_id": "RM-014",
            "channel": "branch",
            "account_status": "active",
            "arrears_amount": "1250.50",
        }

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [
            ("MOMO", "mobile_money"),
            ("Mobile Money", "mobile_money"),
            ("mobile-money", "mobile_money"),
            ("Over The Counter", "branch"),
            ("INTERNET_BANKING", "internet_banking"),
            ("Call Center", "call_centre"),
            ("  agent  ", "agent"),
        ],
    )
    def test_channel_resolves_to_one_spelling(self, sent: str, stored: str) -> None:
        assert normalized(channel=sent)["channel"] == stored

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [
            ("Active", "active"),
            ("frozen", "blocked"),
            ("WRITE OFF", "written_off"),
            ("Dormant", "dormant"),
            ("open", "active"),
        ],
    )
    def test_account_status_resolves_to_one_spelling(self, sent: str, stored: str) -> None:
        assert normalized(account_status=sent)["account_status"] == stored

    def test_officer_id_is_trimmed_and_internal_whitespace_collapsed(self) -> None:
        assert normalized(officer_id="  RM   014 ")["officer_id"] == "RM 014"

    def test_officer_id_keeps_the_banks_own_case_and_punctuation(self) -> None:
        """An open identifier is matched against the bank's own register, so
        case-folding it would make the platform's key differ from the bank's —
        the same decision as ``branch_id``."""
        assert normalized(officer_id="rm-014")["officer_id"] == "rm-014"
        assert normalized(officer_id="RM-014")["officer_id"] == "RM-014"

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [
            (1250.5, "1250.5"),
            ("1,250.50", "1250.50"),
            (900, "900"),
            (0, "0"),
            (Decimal("12.3400"), "12.3400"),
        ],
    )
    def test_arrears_amount_is_kept_as_an_exact_decimal_string(
        self, sent: object, stored: str
    ) -> None:
        assert normalized(arrears_amount=sent)["arrears_amount"] == stored

    def test_keys_are_matched_case_insensitively_and_stored_canonically(self) -> None:
        assert normalized(Officer_ID="RM-014", CHANNEL="atm", Account_Status="DORMANT") == {
            "officer_id": "RM-014",
            "channel": "atm",
            "account_status": "dormant",
        }

    def test_every_vocabulary_value_is_accepted_as_itself(self) -> None:
        for value in CHANNELS:
            assert normalized(channel=value)["channel"] == value
        for value in ACCOUNT_STATUSES:
            assert normalized(account_status=value)["account_status"] == value

    def test_every_alias_resolves_into_its_vocabulary(self) -> None:
        for alias, target in CHANNEL_ALIASES.items():
            assert target in CHANNELS, alias
            assert normalized(channel=alias)["channel"] == target
        for alias, target in ACCOUNT_STATUS_ALIASES.items():
            assert target in ACCOUNT_STATUSES, alias
            assert normalized(account_status=alias)["account_status"] == target

    def test_no_alias_shadows_a_vocabulary_value(self) -> None:
        assert not set(CHANNEL_ALIASES) & set(CHANNELS)
        assert not set(ACCOUNT_STATUS_ALIASES) & set(ACCOUNT_STATUSES)


class TestMalformedValuesAreRefusedWithAnActionableMessage:
    def test_unknown_channel_names_the_value_and_the_vocabulary(self) -> None:
        message = refusal(channel="carrier pigeon")
        assert "channel" in message
        assert "'carrier pigeon'" in message
        for value in CHANNELS:
            assert value in message
        assert "not stored" in message

    def test_unknown_account_status_names_the_value_and_the_vocabulary(self) -> None:
        message = refusal(account_status="ZOMBIE")
        assert "'ZOMBIE'" in message
        for value in ACCOUNT_STATUSES:
            assert value in message

    @pytest.mark.parametrize("ambiguous", ["mobile", "card"])
    def test_deliberately_unresolved_channel_spellings_are_refused(self, ambiguous: str) -> None:
        """``mobile`` could be the app, the wallet or USSD and ``card`` could be
        an ATM or a terminal; resolving either would invent the bank's meaning."""
        assert "not a recognised value" in refusal(channel=ambiguous)

    @pytest.mark.parametrize("separators", ["---", "___", " . "])
    def test_a_value_of_only_separators_is_refused_as_empty(self, separators: str) -> None:
        """Not absence: the source stated something, and what it stated is not a
        value. The message says to omit the key instead."""
        message = refusal(channel=separators)
        assert "sent empty" in message
        assert "omit the key" in message

    def test_suspended_is_refused_rather_than_read_as_blocked(self) -> None:
        """On a loan ``suspended`` commonly means interest is in suspense, which
        is not the same statement as an account that cannot transact."""
        assert "not a recognised value" in refusal(account_status="suspended")

    def test_unreadable_arrears_amount_says_what_to_send_and_in_which_currency(self) -> None:
        message = refusal(arrears_amount="N/A")
        assert "'N/A'" in message
        assert "JSON number" in message
        assert "position's own currency" in message

    def test_negative_arrears_amount_is_refused(self) -> None:
        message = refusal(arrears_amount=-500)
        assert "negative" in message
        assert "Send 0 only if you mean" in message

    def test_non_finite_arrears_amount_is_refused(self) -> None:
        assert "finite" in refusal(arrears_amount="NaN")
        assert "finite" in refusal(arrears_amount="Infinity")

    @pytest.mark.parametrize("key", OPTIONAL_POSITION_ATTRIBUTE_KEYS)
    def test_a_structured_value_is_refused_for_every_key(self, key: str) -> None:
        message = refusal(**{key: {"nested": 1}})
        assert key in message
        assert "received a JSON dict" in message

    def test_over_long_officer_id_names_the_limit(self) -> None:
        message = refusal(officer_id="X" * (OFFICER_ID_MAX_LENGTH + 1))
        assert str(OFFICER_ID_MAX_LENGTH) in message
        assert str(OFFICER_ID_MAX_LENGTH + 1) in message

    def test_officer_id_at_the_limit_is_accepted(self) -> None:
        code = "X" * OFFICER_ID_MAX_LENGTH
        assert normalized(officer_id=code)["officer_id"] == code

    def test_officer_id_with_control_characters_is_refused(self) -> None:
        assert "control characters" in refusal(officer_id="RM-\x00014")

    def test_a_refused_value_leaves_the_rest_of_the_bag_untouched(self) -> None:
        values, problems = normalize_attributes(
            {"channel": "pigeon", "branch_id": "BR-01", "sector": "agriculture"}
        )
        assert values == {"branch_id": "BR-01", "sector": "agriculture"}
        assert [key for key, _ in problems] == ["channel"]


class TestAbsenceIsNotADefect:
    def test_a_position_with_no_attributes_is_unchanged(self) -> None:
        records = CanonicalRecords(positions=[position()])
        assert normalize_positions(records) == ()
        assert records.positions[0].attributes == {}

    def test_a_bag_with_none_of_the_four_keys_is_returned_verbatim(self) -> None:
        bag = {"branch_id": "BR-01", "sector": "agriculture", "ecl_provision_ghs": "12.5"}
        records = CanonicalRecords(positions=[position(attributes=dict(bag))])
        assert normalize_positions(records) == ()
        assert records.positions[0].attributes == bag

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_cell_is_absence_not_a_defect(self, blank: object) -> None:
        """A CSV row simply has no value in that column; that is not something an
        integration engineer needs to be told about."""
        values, problems = normalize_attributes({"channel": blank, "arrears_amount": blank})
        assert values == {}
        assert problems == ()

    def test_unstated_arrears_reads_as_unstated_never_as_zero(self) -> None:
        assert stated_arrears_amount(position()) is None
        assert stated_arrears_amount(position(attributes={"branch_id": "BR-01"})) is None
        assert stated_arrears_amount(position(attributes={"arrears_amount": "0"})) == Decimal(0)


class TestValidationReportsWhatWasDropped:
    def _run(self, *positions: PositionData, severity: str = "WARNING"):
        records = CanonicalRecords(positions=list(positions))
        problems = normalize_positions(records)
        return run_validation(
            records,
            ValidationConfig(
                rules=[RuleConfig(name="optional_position_attributes", severity=severity)]  # type: ignore[arg-type]
            ),
            ValidationContext(as_of_date=AS_OF, attribute_problems=problems),
        )

    def test_a_dropped_value_becomes_a_finding_on_its_own_position(self) -> None:
        outcome = self._run(position("LN-7", attributes={"channel": "pigeon"}))
        (finding,) = outcome.findings
        assert finding.rule == "optional_position_attributes"
        assert finding.source_reference == "LN-7"
        assert finding.source_locator == "source.json#position!LN-7"
        assert finding.severity == "WARNING"
        assert outcome.record_statuses[("position", "LN-7")] == "warning"

    def test_the_position_still_lands_in_every_calculation(self) -> None:
        """``warning`` is an included validation status: the unusable value was
        dropped, not the facility."""
        outcome = self._run(position("LN-7", attributes={"channel": "pigeon"}))
        assert outcome.record_statuses[("position", "LN-7")] in INCLUDED_VALIDATION_STATUSES
        assert outcome.overall_status == "accepted_with_warnings"

    def test_severity_is_per_institution_configuration(self) -> None:
        outcome = self._run(position("LN-7", attributes={"channel": "pigeon"}), severity="ERROR")
        (finding,) = outcome.findings
        assert finding.severity == "ERROR"
        assert outcome.record_statuses[("position", "LN-7")] == "error"

    def test_one_finding_per_unusable_value(self) -> None:
        outcome = self._run(
            position(
                "LN-7",
                attributes={
                    "channel": "pigeon",
                    "account_status": "zombie",
                    "arrears_amount": "-1",
                },
            )
        )
        assert len(outcome.findings) == 3
        assert {finding.rule for finding in outcome.findings} == {"optional_position_attributes"}

    def test_a_clean_batch_under_the_default_config_says_nothing(self) -> None:
        records = CanonicalRecords(
            positions=[
                position(
                    attributes={
                        "officer_id": "RM-014",
                        "channel": "branch",
                        "account_status": "active",
                        "arrears_amount": "10",
                    }
                )
            ]
        )
        problems = normalize_positions(records)
        outcome = run_validation(
            records,
            default_validation_config(),
            ValidationContext(as_of_date=AS_OF, attribute_problems=problems),
        )
        assert outcome.findings == []
        assert outcome.overall_status == "accepted"


class TestConsistencyRuleFires:
    def _run(self, *positions: PositionData):
        records = CanonicalRecords(positions=list(positions))
        normalize_positions(records)
        return run_validation(
            records,
            ValidationConfig(
                rules=[RuleConfig(name="position_attribute_consistency", severity="INFO")]
            ),
            ValidationContext(as_of_date=AS_OF),
        )

    def test_arrears_above_balance_is_reported(self) -> None:
        outcome = self._run(
            position("LN-1", balance=Decimal("100"), attributes={"arrears_amount": "100000"})
        )
        (finding,) = outcome.findings
        assert "exceeds the outstanding balance" in finding.detail
        assert finding.severity == "INFO"

    def test_arrears_equal_to_balance_is_normal(self) -> None:
        assert (
            self._run(
                position("LN-1", balance=Decimal("100"), attributes={"arrears_amount": "100"})
            ).findings
            == []
        )

    def test_arrears_on_a_position_with_no_repayment_schedule_is_reported(self) -> None:
        outcome = self._run(
            position("DP-1", position_type="DEPOSIT", attributes={"arrears_amount": "10"})
        )
        (finding,) = outcome.findings
        assert "no repayment schedule" in finding.detail

    def test_closed_account_with_a_balance_is_reported(self) -> None:
        outcome = self._run(
            position("LN-1", balance=Decimal("500"), attributes={"account_status": "closed"})
        )
        (finding,) = outcome.findings
        assert "closed" in finding.detail

    def test_closed_account_with_no_balance_is_silent(self) -> None:
        assert (
            self._run(
                position("LN-1", balance=Decimal("0"), attributes={"account_status": "closed"})
            ).findings
            == []
        )

    def test_a_consistent_position_is_silent(self) -> None:
        assert (
            self._run(
                position(
                    "LN-1",
                    balance=Decimal("1000"),
                    attributes={"arrears_amount": "250", "account_status": "active"},
                )
            ).findings
            == []
        )

    def test_the_rule_never_changes_a_figure(self) -> None:
        records = CanonicalRecords(
            positions=[position("LN-1", balance=Decimal("100"), attributes={"arrears_amount": "9"})]
        )
        normalize_positions(records)
        run_validation(
            records,
            ValidationConfig(
                rules=[RuleConfig(name="position_attribute_consistency", severity="INFO")]
            ),
            ValidationContext(as_of_date=AS_OF),
        )
        assert records.positions[0].attributes["arrears_amount"] == "9"
        assert records.positions[0].balance == Decimal("100")
