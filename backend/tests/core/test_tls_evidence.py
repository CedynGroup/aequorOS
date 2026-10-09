"""Evidence CLI uses the services' effective database configuration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest

from app.core import tls_evidence
from app.core.config import get_operator_settings, get_settings
from app.core.tls import TransportSecurityError

DATABASE_NAMES = ("DATABASE_URL", "WORKER_DATABASE_URL", "BI_DATABASE_URL", "OPERATOR_DATABASE_URL")


@pytest.mark.parametrize("source", ["dotenv", "environment", "override"])
@pytest.mark.parametrize("failed_role", [None, *DATABASE_NAMES])
def test_cli_probes_all_configured_roles_and_reports_database_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    failed_role: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    urls = {
        name: f"postgresql+psycopg://synthetic:secret@db.example/{index}?sslmode=verify-full"
        for index, name in enumerate(DATABASE_NAMES)
    }
    (tmp_path / ".env").write_text(
        "\n".join(f"{name}={url}" for name, url in urls.items()) if source != "environment" else ""
    )
    for name, url in urls.items():
        if source == "environment":
            monkeypatch.setenv(name, url)
        else:
            monkeypatch.delenv(name, raising=False)
    if source == "override":
        urls["WORKER_DATABASE_URL"] = "postgresql+psycopg://u:secret@override.example/worker"
        monkeypatch.setenv("WORKER_DATABASE_URL", urls["WORKER_DATABASE_URL"])
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    probed: list[str] = []

    def probe_database(url: str) -> dict[str, str | bool | None]:
        probed.append(url)
        if failed_role is not None and url == urls[failed_role]:
            raise TransportSecurityError(f"sensitive error: {url}")
        return {"negotiated_version": "TLSv1.3"}

    def probe_https(_url: str) -> dict[str, str]:
        return {"negotiated_version": "TLSv1.3"}

    monkeypatch.setattr(tls_evidence, "probe_database", probe_database)
    monkeypatch.setattr(tls_evidence, "probe_https", probe_https)
    monkeypatch.setattr(
        sys, "argv", ["tls_evidence", "--https", "https://edge.example", "--databases"]
    )
    try:
        if failed_role is None:
            tls_evidence.main()
        else:
            with pytest.raises(SystemExit) as error:
                tls_evidence.main()
            assert error.value.code == 1
        assert probed == list(urls.values())
        output = capsys.readouterr().out
        evidence = cast(dict[str, object], json.loads(output))
        results = cast(list[dict[str, object]], evidence["results"])
        assert [result["target"] for result in results] == ["https-1", *DATABASE_NAMES]
        failures = [result for result in results if "error" in result]
        assert failures == (
            []
            if failed_role is None
            else [{"target": failed_role, "error": "TransportSecurityError"}]
        )
        assert "secret" not in output
        assert "postgresql" not in output
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()
