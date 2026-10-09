"""Security semantics and executable healthchecks of the deployment templates."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def compose(path: str) -> dict[str, object]:
    return mapping(cast(object, yaml.safe_load((ROOT / path).read_text())))


def test_backend_workers_receive_trust_roots_without_listener_keys() -> None:
    for file in ("backend/docker-compose.prod.yml", "backend/docker-compose.ai.prod.yml"):
        configuration = compose(file)
        services = mapping(configuration["services"])
        for name, value in services.items():
            service = mapping(value)
            environment = mapping(service["environment"])
            assert environment["APP_ENV"] == "production"
            assert environment["TLS_ALLOW_PLAINTEXT"] == "0"
            assert environment["TLS_CA_BUNDLE"] == "/run/trust/ca.pem"
            volumes = cast(list[str], service["volumes"])
            assert "service-trust:/run/trust:ro" in volumes
            if name.startswith("risk-worker") or name == "risk-migrate":
                assert volumes == ["service-trust:/run/trust:ro"]
        declarations = mapping(configuration["volumes"])
        assert all(mapping(volume).get("external") is True for volume in declarations.values())


@pytest.mark.parametrize("exit_code,expected", [(0, 0), (2, 0), (1, 1)])
def test_openbao_healthcheck_distinguishes_sealed_from_transport_failure(
    tmp_path: Path, exit_code: int, expected: int
) -> None:
    configuration = compose("deploy/openbao/docker-compose.openbao.yml")
    service = mapping(mapping(configuration["services"])["openbao"])
    environment = mapping(service["environment"])
    assert environment["BAO_ADDR"] == "https://openbao:8200"
    assert environment["BAO_CACERT"] == "/run/trust/ca.pem"
    healthcheck = mapping(service["healthcheck"])
    command = cast(list[str], healthcheck["test"])
    assert command[0] == "CMD-SHELL"
    executable = tmp_path / "bao"
    executable.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    executable.chmod(0o700)
    result = subprocess.run(["/bin/sh", "-c", command[1]], env={"PATH": str(tmp_path)}, check=False)
    assert result.returncode == expected
