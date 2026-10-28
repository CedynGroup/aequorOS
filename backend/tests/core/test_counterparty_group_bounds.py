import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.key_management.local import LocalKeyProvider
from app.domain.ingestion.contracts import CounterpartyData
from tests.core.test_bank_field_encryption import connect, counterparty, provider

__all__ = ["provider"]


@pytest.mark.parametrize("axis", ["group_reference", "group", "parent", "attribute_reference"])
@pytest.mark.parametrize("connected", [False, True])
def test_group_reference_bounds_apply_to_input_and_persistence(
    db_session: Session,
    provider: LocalKeyProvider,
    connected: bool,
    axis: str,
) -> None:
    if connected:
        connect(db_session, provider)
    attribute = "group_reference" if axis == "attribute_reference" else axis
    for length in (255, 256, 300):
        group = "G" * length
        data: dict[str, object] = {
            "source_reference": "CP1",
            "source_locator": "row",
            "name": "A",
            "counterparty_type": "RETAIL_INDIVIDUAL",
            "group_reference": group if axis == "group_reference" else None,
            "attributes": {} if axis == "group_reference" else {attribute: group},
        }
        if length > 255:
            with pytest.raises(ValidationError):
                CounterpartyData.model_validate(data)
        else:
            assert CounterpartyData.model_validate(data).name == "A"
    party = counterparty(db_session, include_package=False)
    party.group_reference = "G" * 255 if axis == "group_reference" else None
    party.attributes = {} if axis == "group_reference" else {attribute: "G" * 255}
    db_session.commit()
    for length in (256, 300):
        with pytest.raises(ValueError, match="255"), db_session.begin_nested():
            if axis == "group_reference":
                party.group_reference = "G" * length
            else:
                party.attributes = {attribute: "G" * length}
            db_session.flush()
