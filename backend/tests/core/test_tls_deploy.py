"""Security semantics and executable healthchecks of the deployment templates."""

from __future__ import annotations

import json
import os
import shutil
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
@pytest.mark.parametrize("host_result", [None, "1"])
def test_openbao_healthcheck_distinguishes_sealed_from_transport_failure(
    tmp_path: Path, exit_code: int, expected: int, host_result: str | None
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is required to normalize the deployment healthcheck")
    environment_variables = dict(os.environ)
    environment_variables.pop("result", None)
    if host_result is not None:
        environment_variables["result"] = host_result
    normalized = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "--project-directory",
            str(tmp_path),
            "-f",
            str(ROOT / "deploy/openbao/docker-compose.openbao.yml"),
            "config",
            "--format",
            "json",
        ],
        env=environment_variables,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    configuration = mapping(cast(object, json.loads(normalized.stdout)))
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
    result = subprocess.run(
        ["/bin/sh", "-c", command[1].replace("$$", "$")],
        env={"PATH": str(tmp_path)},
        check=False,
    )
    assert result.returncode == expected
