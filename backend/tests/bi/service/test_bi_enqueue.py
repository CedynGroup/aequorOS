"""The ONE ``bi_mart_refresh`` enqueue seam and the product hooks that call it.

``app.bi.service.enqueue.enqueue_mart_refresh`` is inert unless
``BI_MART_ENQUEUE_ENABLED`` (pinned ``"0"`` in ``tests/conftest.py``), and
when on it queues exactly one coalesced job per ``(bank, as_of)`` with the
contract payload. The hook sites — ingestion, the live-plane triggers — are
proven here through their real entrypoints; the withdrawal and pipeline hooks
are proven beside the tests that already drive those paths
(``tests/data_engine/api/test_system_of_record_integration.py``,
``tests/live/service/test_pipeline_integration.py``).

This module's other-suite cases live in ``test_bi_enqueue_integration.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.bi.jobs import bi_common
from app.bi.service import enqueue, versions
from app.models import BiFactPositionEom


def test_the_version_stamp_is_the_shared_constant_the_handlers_compare() -> None:
    """The enqueue side and the builder agree on ONE integer, defined in a
    module with no imports so the hot path never loads the builder. The
    builder module must re-export it under the contract name."""
    assert isinstance(versions.BUILDER_VERSION, int)
    assert versions.BUILDER_VERSION >= 1
    builder = bi_common.load_builder()
    assert builder.BUILDER_VERSION == versions.BUILDER_VERSION


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_seam_does_not_import_the_builder() -> None:
    """The one structural guarantee of the module docstring, as a test: the
    hot path loads the version constant, the queue and the two BI models the
    due-check reads — never the builder, the catalogue, the compiler or the
    handler tree.

    ``app.bi.models.marts`` is deliberately NOT forbidden (A5-04/D-043 moved the
    sweep's read here so ``scheduler.py`` imports no BI model) and it costs the
    hot path nothing: ``app/models/__init__.py`` already loads it for every
    consumer of ``app.models``."""
    imported = _imported_modules(Path(enqueue.__file__))
    forbidden = {
        name
        for name in imported
        if name.startswith(
            (
                "app.bi.service.mart_builder",
                "app.bi.service.compiler",
                "app.bi.service.partitions",
                "app.bi.domain",
                "app.jobs",
            )
        )
    }
    assert forbidden == set(), forbidden
    assert "app.bi.service.versions" in imported
    # And the constants module itself imports nothing at all.
    assert _imported_modules(Path(versions.__file__)) == {"__future__", "__future__.annotations"}


def test_the_regulatory_plane_imports_only_the_seam_and_the_versions_module() -> None:
    """D-041/D-043 for the five hook sites, ahead of wave 3's boundary test:
    the sweep's BI-model read moved into the seam, so ``scheduler.py`` — the
    one module that used to import ``app.bi.models.marts`` (A5-04) — imports only
    ``enqueue`` and ``versions``."""
    hook_sites = (
        "app/services/scheduler.py",
        "app/data_engine/service/ingestion.py",
        "app/live/service/pipeline.py",
        "app/live/service/live_refresh_triggers.py",
        "app/data_engine/service/canonical_withdrawal.py",
    )
    allowed = {"app.bi.service.enqueue", "app.bi.service.versions"}
    offenders: dict[str, set[str]] = {}
    for relative in hook_sites:
        imported = _imported_modules(Path(enqueue.__file__).parents[3] / relative)
        bi_imports = {
            name
            for name in imported
            if name.startswith(("app.bi.service", "app.bi.domain", "app.bi.models.marts"))
            and name not in allowed
            # ``from app.bi.service.enqueue import x`` also yields the dotted
            # member name; only the MODULE matters here.
            and not any(name.startswith(f"{ok}.") for ok in allowed)
        }
        if bi_imports:
            offenders[relative] = bi_imports
    assert offenders == {}, offenders


def test_retention_may_name_only_the_daily_parents() -> None:
    """D-039 at the constant level: the month-end fact is kept forever, and
    the BI service layer exposes no yearly drop at all."""
    from app.bi.service import mart_builder, partitions  # noqa: PLC0415

    eom = BiFactPositionEom.__tablename__
    assert eom not in mart_builder.RETENTION_PARENTS
    assert set(mart_builder.RETENTION_PARENTS) <= set(partitions.MONTHLY_BUILD_PARENTS)
    assert eom in partitions.YEARLY_BUILD_PARENTS
    assert not hasattr(partitions, "drop_year_partition")
    assert "drop_year_partition" not in partitions.__all__
