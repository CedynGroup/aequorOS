from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import json

import pytest
from botocore.exceptions import ClientError
from pydantic import TypeAdapter

from app.core.audit_integrity import AlertClient, publish_alert
from app.core.config import get_settings
from app.core.observability import Condition

TOPIC = "arn:aws:sns:eu-west-1:123456789012:synthetic-on-call"


class Publisher:
    class Metadata:
        endpoint_url: str = "https://sns.eu-west-1.amazonaws.com"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.meta: Publisher.Metadata = self.Metadata()
        self.messages: list[dict[str, str]] = []

    def publish(self, *, TopicArn: str, Message: str, Subject: str) -> object:
        if self.fail:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "private provider detail"}}, "Publish"
            )
        self.messages.append({"topic": TopicArn, "message": Message, "subject": Subject})
        return {"MessageId": "synthetic"}


def test_broken_chain_publishes_safe_incident_to_configured_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ALERT_TOPIC_ARN", TOPIC)
    get_settings.cache_clear()
    publisher = Publisher()
    regions: list[object] = []

    def factory(*_args: object, **kwargs: object) -> AlertClient:
        regions.append(kwargs["region_name"])
        return publisher

    monkeypatch.setattr("app.core.audit_integrity.boto3.client", factory)
    assert publish_alert("chain_mismatch", "audit_events")
    assert regions == ["eu-west-1"]
    assert len(publisher.messages) == 1
    assert publisher.messages[0]["topic"] == TOPIC
    assert TypeAdapter(dict[str, str]).validate_python(
        json.loads(publisher.messages[0]["message"])
    ) == {"condition": "audit.chain_broken", "reason": "chain_mismatch", "stream": "audit_events"}


@pytest.mark.parametrize("configured", [False, True])
def test_missing_or_failed_pager_is_visible_without_raw_provider_errors(
    monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ALERT_TOPIC_ARN", TOPIC if configured else "")
    get_settings.cache_clear()
    publisher = Publisher(fail=True)
    conditions: list[tuple[Condition, dict[str, object]]] = []

    def factory(*_args: object, **_kwargs: object) -> AlertClient:
        return publisher

    def capture(condition: Condition, _message: str, **fields: object) -> None:
        conditions.append((condition, fields))

    monkeypatch.setattr("app.core.audit_integrity.boto3.client", factory)
    monkeypatch.setattr("app.core.audit_integrity.emit", capture)
    assert not publish_alert("verification_unavailable")
    assert conditions == [
        (
            Condition.AUDIT_CHAIN_BROKEN,
            {
                "severity": "error",
                "reason": "paging_failed" if configured else "paging_unconfigured",
            },
        )
    ]


def test_plaintext_sdk_override_refuses_page_without_publishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ALERT_TOPIC_ARN", TOPIC)
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    get_settings.cache_clear()
    publisher = Publisher()
    publisher.meta.endpoint_url = "http://sns.example.test"

    def factory(*_args: object, **_kwargs: object) -> AlertClient:
        return publisher

    monkeypatch.setattr("app.core.audit_integrity.boto3.client", factory)
    assert not publish_alert("chain_mismatch", "audit_events")
    assert publisher.messages == []
