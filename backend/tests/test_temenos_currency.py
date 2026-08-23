from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.temenos import TemenosConnection
from app.schemas.temenos_connections import TemenosConnectionCreate


def test_temenos_connection_create_requires_a_reporting_currency() -> None:
    with pytest.raises(ValidationError, match="default_currency"):
        TemenosConnectionCreate.model_validate(
            {
                "connection_mode": "OFS",
                "display_name": "Core OFS",
                "endpoint": "ofs://core.example.test",
            }
        )


def test_temenos_connection_model_has_no_currency_server_default() -> None:
    column = TemenosConnection.__table__.c.default_currency

    assert column.nullable is False
    assert column.default is None
    assert column.server_default is None