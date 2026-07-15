"""Test configuration: force the reduced model config and isolate artifacts.

Environment variables must be set before any ``app`` module instantiates
``Settings``, so this happens at conftest import time (pytest imports conftest
before collecting test modules).
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CASHFLOW_FAST_TEST", "1")
os.environ["CASHFLOW_ARTIFACTS_DIR"] = tempfile.mkdtemp(prefix="cashflow-ml-artifacts-")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    # Deferred so the environment above is set before Settings is instantiated.
    from app.main import app  # noqa: PLC0415

    with TestClient(app) as test_client:
        yield test_client
