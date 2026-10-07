"""Moving a model's code must never change the order a session flushes rows in.

SQLAlchemy orders the INSERTs, UPDATEs and DELETEs of mappers that share no
``relationship()`` by each mapper's ``"module.ClassName"`` key, and this codebase
declares no relationships. Splitting ``Bank`` out of ``app/models/regulatory.py``
gave it the key ``app.models.bank.Bank``, which sorts before
``app.models.organization.Organization``: a flush adding an organization and its
bank then inserted the bank first and broke the foreign key
(``tests/scripts/test_authorization_access_impact.py`` caught it).

``app/db/flush_order.json`` pins every model's key to the module it was recorded
in, so the feature layout can move model files freely. These tests keep that file
complete and in force.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapper

import app.models
from app.db.base import FLUSH_ORDER, Base

_ = app.models


def _app_mappers() -> list[Mapper]:
    return [m for m in Base.registry.mappers if m.class_.__module__.startswith("app.")]


def test_every_model_has_a_pinned_flush_order_key() -> None:
    missing = sorted(
        f'"{m.class_.__name__}": "{m.class_.__module__}.{m.class_.__name__}"'
        for m in _app_mappers()
        if m.class_.__name__ not in FLUSH_ORDER
    )
    assert missing == [], f"Add these entries to app/db/flush_order.json: {missing}"


def test_the_pinned_keys_are_in_force_and_none_is_stale() -> None:
    mappers = {m.class_.__name__: m for m in _app_mappers()}
    assert sorted(FLUSH_ORDER) == sorted(mappers), "flush_order.json names a model that is gone"
    for name, key in FLUSH_ORDER.items():
        assert mappers[name]._sort_key == key, name


def test_a_moved_model_keeps_the_key_of_its_old_module() -> None:
    """Negative control: without the pin, ``Bank`` would sort before ``Organization``."""
    bank = next(m for m in _app_mappers() if m.class_.__name__ == "Bank")
    assert bank.class_.__module__ == "app.models.bank"
    assert bank._sort_key == "app.models.regulatory.Bank"
    assert FLUSH_ORDER["Organization"] < bank._sort_key
    assert FLUSH_ORDER["Organization"] > f"{bank.class_.__module__}.Bank"
