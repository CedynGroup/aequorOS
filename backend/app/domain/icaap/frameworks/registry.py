"""Discovery, digest and applicability for ICAAP frameworks (pure domain).

The digest is taken over the parsed SOURCE document, not over the objects the
parser builds: re-indenting the JSON or reordering its keys leaves it unchanged,
while any edit to the content changes it. A cycle pins ``(code, version,
digest)`` when it is created, so editing a published framework version in place
is detectable — and readiness reports it as blocking rather than quietly
re-basing a bank's checklist under it.

**A published version is never edited in place.** A corrected extraction ships
as a new version with a ``section_key_map``, and cycles move by rebase.
"""

from __future__ import annotations

import functools
import hashlib
import json
from datetime import date
from pathlib import Path

from app.domain.icaap.frameworks.schema import (
    Framework,
    FrameworkSchemaError,
    parse_framework,
)

FRAMEWORK_ROOT = Path(__file__).parent


class FrameworkNotFound(LookupError):
    """No framework is published under that code and version."""


def canonical_json(payload: object) -> str:
    """Byte-identical to ``services/attestation/digests.canonical_json``."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def framework_digest(raw: object) -> str:
    return hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()


def load_all(roots: tuple[Path, ...] = (FRAMEWORK_ROOT,)) -> tuple[Framework, ...]:
    """Every framework JSON under ``<root>/<jurisdiction>/*.json``."""
    frameworks: list[Framework] = []
    seen: dict[tuple[str, str], Path] = {}
    for root in roots:
        for path in sorted(root.glob("*/*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            framework = parse_framework(raw, digest=framework_digest(raw))
            if framework.jurisdiction.lower() != path.parent.name:
                msg = (
                    f"{path}: jurisdiction {framework.jurisdiction!r} does not match "
                    f"directory {path.parent.name!r}"
                )
                raise FrameworkSchemaError(str(path), msg)
            key = (framework.code, framework.version)
            if key in seen:
                raise FrameworkSchemaError(str(path), f"{key} is already published by {seen[key]}")
            seen[key] = path
            frameworks.append(framework)
    return tuple(frameworks)


#: Additional directories of framework JSON, INJECTED by the service layer.
#:
#: This module stays free of settings (the same reason ``applicable`` takes
#: ``enabled_codes`` rather than reading them): whether a deployment may load
#: an unreviewed framework is a deployment question, answered once in
#: ``app/services/icaap/frameworks.py`` against the environment allow-list.
#: Here it is just a list of directories somebody else decided on.
_extra_roots: tuple[Path, ...] = ()


def extra_roots() -> tuple[Path, ...]:
    return _extra_roots


def set_extra_roots(roots: tuple[Path, ...]) -> None:
    """Publish additional framework directories, dropping the load cache."""
    global _extra_roots  # noqa: PLW0603 - one module-level registry, set once per process
    if roots == _extra_roots:
        return
    _extra_roots = roots
    reset_cache()


def roots() -> tuple[Path, ...]:
    return (FRAMEWORK_ROOT, *_extra_roots)


@functools.cache
def _default() -> tuple[Framework, ...]:
    return load_all(roots())


def all_frameworks() -> tuple[Framework, ...]:
    return _default()


def get(code: str, version: str) -> Framework:
    for framework in _default():
        if framework.code == code and framework.version == version:
            return framework
    msg = f"No ICAAP framework {code!r} version {version!r} is published."
    raise FrameworkNotFound(msg)


def versions(code: str) -> tuple[Framework, ...]:
    """Every published version of one framework, newest first."""
    matching = [framework for framework in _default() if framework.code == code]
    return tuple(sorted(matching, key=lambda f: (f.effective_from, f.version), reverse=True))


def _superseded_for(framework: Framework, published: tuple[Framework, ...], as_of: date) -> bool:
    """True when a later version of this instrument already covers ``as_of``."""
    for candidate in published:
        if candidate is framework or candidate.supersedes is None:
            continue
        if candidate.supersedes != (framework.code, framework.version):
            continue
        if candidate.first_as_of_date <= as_of:
            return True
    return False


def applicable(
    jurisdiction: str,
    institution_class: str,
    as_of: date,
    *,
    cycle_kind: str,
    enabled_codes: frozenset[str] | None = None,
) -> tuple[Framework, ...]:
    """The frameworks a new cycle may pin, newest first.

    A rehearsal is date-agnostic on purpose: the founder's acceptance scenario
    is a FY2025 dry run against the 2026 exposure draft, which is exactly what a
    bank rehearsing a new instrument wants. A real filing cycle may only pin a
    framework that already covers its as-of date and has not been superseded for
    it.

    ``enabled_codes`` is the deployment's ``ICAAP_FRAMEWORKS_ENABLED`` list,
    passed in by the service so this module stays free of settings.
    """
    published = _default()
    matches: list[Framework] = []
    for framework in published:
        if enabled_codes is not None and framework.code not in enabled_codes:
            continue
        if framework.jurisdiction.upper() != jurisdiction.upper():
            continue
        if institution_class not in framework.institution_classes:
            continue
        if cycle_kind != "rehearsal":
            if as_of < framework.first_as_of_date:
                continue
            if _superseded_for(framework, published, as_of):
                continue
        matches.append(framework)
    return tuple(sorted(matches, key=lambda f: (f.effective_from, f.version), reverse=True))


def reset_cache() -> None:
    """Drop the load cache. Tests that publish a synthetic framework use it."""
    _default.cache_clear()


__all__ = [
    "FRAMEWORK_ROOT",
    "FrameworkNotFound",
    "all_frameworks",
    "applicable",
    "canonical_json",
    "extra_roots",
    "framework_digest",
    "get",
    "load_all",
    "reset_cache",
    "roots",
    "set_extra_roots",
    "versions",
]
