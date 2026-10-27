"""Canonical outcomes and change evidence for production audit records."""

from pydantic import JsonValue

EVENT_OUTCOMES = {
    "regulatory_run.succeeded": "success",
    "regulatory_run.failed": "failure",
    "ingestion_batch.accepted": "success",
    "ingestion_batch.accepted_with_warnings": "success",
    "ingestion_batch.failed": "failure",
    "ingestion_batch.rejected": "failure",
    "regulatory_package.status_changed": "success",
    "position_snapshot.overridden": "success",
    "ml_etl.dedup_completed": "success",
    "ml_etl.dedup_failed": "failure",
}


def normalize_event_details(action: str, details: dict[str, JsonValue]) -> dict[str, JsonValue]:
    normalized = dict(details)
    if action in EVENT_OUTCOMES and normalized.get("outcome") is None:
        normalized["outcome"] = EVENT_OUTCOMES[action]
    for destination, source in (("before", "from_status"), ("after", "to_status")):
        if destination not in normalized and source in details:
            normalized[destination] = {"status": details[source]}
    field = details.get("field")
    if isinstance(field, str):
        for destination, source in (("before", "original_value"), ("after", "new_value")):
            if destination not in normalized and source in details:
                normalized[destination] = {field: details[source]}
    return normalized
