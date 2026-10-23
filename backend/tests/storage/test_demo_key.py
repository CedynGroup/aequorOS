from __future__ import annotations

import stat
from pathlib import Path
from typing import Never

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import is_undeployed_environment
from app.core.key_management import demo, registry
from app.core.key_management.aws import AwsKmsKeyProvider
from app.core.key_management.demo import FileDemoKeyProvider, change_status, initialize
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.settings import KeyManagementSettings
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.db.base import Base
from app.models import Bank, Organization
from scripts.demo_bank_key import main
from tests.fixtures.reference_data import seed_global_reference_data


def test_control_loop_preserves_key_material_across_provider_instances(tmp_path: Path) -> None:
    path = tmp_path / "demo-key.json"
    state = initialize(path, organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    context = {"bank_id": state.bank_id, "purpose": "synthetic-demo"}
    wrapped = FileDemoKeyProvider(path).generate_data_key(state.reference, context)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for status in (KeyStatus.DISABLED, KeyStatus.UNAVAILABLE, KeyStatus.ACTIVE) * 3:
        change_status(path, status)
        provider = FileDemoKeyProvider(path)
        if status == KeyStatus.ACTIVE:
            assert provider.unwrap(state.reference, wrapped.wrapped, context) == wrapped.plaintext
        else:
            with pytest.raises(KeyUnavailableError) as refused:
                provider.unwrap(state.reference, wrapped.wrapped, context)
            assert refused.value.revoked == (status == KeyStatus.DISABLED)
    with pytest.raises(FileExistsError):
        initialize(path, organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    assert (
        FileDemoKeyProvider(path).unwrap(state.reference, wrapped.wrapped, context)
        == wrapped.plaintext
    )


@pytest.mark.parametrize("environment", ["production", "staging", "demo", "unknown"])
def test_demo_control_refuses_deployed_environments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setattr(
        demo, "is_undeployed_environment", lambda: is_undeployed_environment(environment)
    )
    path = tmp_path / "demo-key.json"
    with pytest.raises(KeyUnavailableError, match="only in local/test"):
        initialize(path, organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    assert not path.exists()
    with pytest.raises(KeyUnavailableError, match="only in local/test"):
        FileDemoKeyProvider(path)


def test_cli_runs_revoke_outage_restore_without_printing_key_material(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = ["--directory", str(tmp_path)]
    assert main(["init", *args, "--organization", "OR-DEMO0001", "--bank", "BK-DEMO0001"]) == 0
    material = demo.read_state(tmp_path / "demo-key.json").material.get_secret_value()
    for command, expected in [
        ("revoke", "disabled"),
        ("outage", "unavailable"),
        ("restore", "active"),
    ]:
        assert main([command, *args]) == 0
        output = capsys.readouterr().out
        assert f"BK-DEMO0001 {expected}" in output and material not in output


def test_provider_resolution_is_explicit_and_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "demo-key.json"
    state = initialize(path, organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    monkeypatch.setattr(
        registry,
        "get_key_settings",
        lambda: KeyManagementSettings(DEMO_BANK_KEY_FILE=path),
    )
    provider = registry.provider_for("aws_kms")
    assert provider.describe(state.reference).status == KeyStatus.ACTIVE
    other = KeyReference("aws_kms", "synthetic:OR-DEMO0001:BK-OTHER001", "local", "000000000000")
    with pytest.raises(KeyUnavailableError, match="not this synthetic demo key"):
        provider.describe(other)
    monkeypatch.setattr(demo, "is_undeployed_environment", lambda: False)
    with pytest.raises(KeyUnavailableError, match="only in local/test"):
        registry.provider_for("aws_kms")


def test_connect_provisions_only_synthetic_key_metadata_in_explicit_sqlite(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'demo.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_global_reference_data(db)
        db.add(Organization(id="OR-DEMO0001", name="Synthetic demo"))
        db.flush()
        db.add(
            Bank(
                id="BK-DEMO0001",
                organization_id="OR-DEMO0001",
                name="Synthetic demo bank",
                short_name="Synthetic",
                jurisdiction_code="GH",
                currency="GHS",
                license_type="universal_bank",
                institution_type="universal_bank",
            )
        )
        db.commit()
    initialize(tmp_path / "demo-key.json", organization_id="OR-DEMO0001", bank_id="BK-DEMO0001")
    for _ in range(2):
        assert main(["connect", "--directory", str(tmp_path)]) == 0
    with Session(engine) as db:
        key = db.scalar(select(BankEncryptionKey))
        assert key is not None and key.bank_id == "BK-DEMO0001"
        assert key.key_id == "synthetic:OR-DEMO0001:BK-DEMO0001"
        assert key.storage_slug == "bk-demo0001"
        bank = db.get(Bank, "BK-DEMO0001")
        assert bank is not None and bank.storage_slug == key.storage_slug
    engine.dispose()


def test_synthetic_reference_cannot_fall_back_to_real_aws(tmp_path: Path) -> None:
    state = initialize(
        tmp_path / "demo-key.json", organization_id="OR-DEMO0001", bank_id="BK-DEMO0001"
    )

    def refuse_network(_region: str) -> Never:
        pytest.fail("A synthetic reference must be refused before creating an AWS client")

    with pytest.raises(KeyUnavailableError, match="exact AWS KMS key ARN"):
        AwsKmsKeyProvider(refuse_network).describe(state.reference)
