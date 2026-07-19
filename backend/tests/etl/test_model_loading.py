"""Per-tenant ML-ETL model persistence + loading (governance: no spillover)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.etl import model_loading
from app.etl.models._mrm import ModelUnavailableError
from app.etl.models.counterparty_matching_model.model import CounterpartyMatchingModel


def test_etl_models_persist_and_load_per_tenant_no_spillover(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Isolate the artifact tree so the test never touches the repo's real one.
    monkeypatch.setattr(model_loading, "DEFAULT_ARTIFACT_DIR", tmp_path)

    org = uuid4()
    bank_a = uuid4()
    bank_b = uuid4()

    # Train a (tiny) model and persist it to bank A's per-tenant path only.
    model = CounterpartyMatchingModel().fit(
        [{"token_sort_ratio": 1.0}, {"token_sort_ratio": 0.0}], [1, 0]
    )
    saved = model.save(model_loading.counterparty_artifact_path(org, bank_a))

    path_a = model_loading.counterparty_artifact_path(org, bank_a)
    path_b = model_loading.counterparty_artifact_path(org, bank_b)
    # Each bank has its own directory; A's artifact never sits in B's path.
    assert path_a == saved
    assert path_a != path_b
    assert str(bank_a) in str(path_a) and str(bank_b) not in str(path_a)
    assert path_a.exists() and not path_b.exists()

    # Loading bank A returns its trained model; loading bank B finds nothing —
    # a model trained for one bank is never served to another.
    loaded_a = CounterpartyMatchingModel.load(path_a)
    assert loaded_a.is_fitted
    with pytest.raises(ModelUnavailableError):
        CounterpartyMatchingModel.load(path_b)
