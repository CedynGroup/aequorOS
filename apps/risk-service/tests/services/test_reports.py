from uuid import uuid4

from app.schemas.common import JsonObject
from app.services.reports import sanitize_report_object


def test_uuid_bearing_object_keys_receive_distinct_stable_aliases() -> None:
    first_id = uuid4()
    second_id = uuid4()
    details: JsonObject = {
        f"finding:{first_id}": {"value": "first"},
        f"finding:{str(first_id).upper()}": {"value": "uppercase"},
        f"finding:{second_id}": {"value": "second"},
    }

    sanitized = sanitize_report_object(details)
    repeated = sanitize_report_object(dict(reversed(details.items())))

    assert len(sanitized) == 3
    assert sanitized == repeated
    assert {"value": "first"} in sanitized.values()
    assert {"value": "uppercase"} in sanitized.values()
    assert {"value": "second"} in sanitized.values()
    assert all(str(first_id) not in key and str(second_id) not in key for key in sanitized)
