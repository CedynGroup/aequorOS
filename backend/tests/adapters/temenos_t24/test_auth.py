"""Auth seam contract: the simulated provider mints a well-formed, secret-free
session offline, and rejects unknown modes."""

from __future__ import annotations

import pytest

from app.adapters.temenos_t24.auth import (
    CONNECTION_MODES,
    SimulatedSessionProvider,
    TemenosCredentials,
)


def test_simulated_sign_on_produces_session_without_network() -> None:
    session = SimulatedSessionProvider().sign_on(
        "OFS", "ofs://bank", TemenosCredentials(username="SVC", password="secret"), company="GH01"
    )
    assert session.mode == "OFS"
    assert session.company == "GH01"
    assert session.token
    assert session.metadata["provider"] == "simulated"


def test_token_never_embeds_the_secret() -> None:
    session = SimulatedSessionProvider().sign_on(
        "OFS", "ofs://bank", TemenosCredentials(username="SVC", password="s3cr3t-PW")
    )
    assert "s3cr3t-PW" not in session.token
    # username (an identifier, not a secret) is fine to echo for traceability
    assert "SVC" in session.token


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown connection mode"):
        SimulatedSessionProvider().sign_on("SOAP", "x", TemenosCredentials())


def test_every_connection_mode_signs_on() -> None:
    provider = SimulatedSessionProvider()
    for mode in CONNECTION_MODES:
        session = provider.sign_on(mode, f"{mode.lower()}://bank", TemenosCredentials(username="u"))
        assert session.mode == mode
