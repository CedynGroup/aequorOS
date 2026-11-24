"""Export the AequorOS Reporting Gateway's practice return catalogue.

Writes one JSON document per return AequorOS can file through the gateway into
``gateway/api/app/returns/catalogue/``, from ``app.reporting.service.gateway_catalog``
(the same definitions that build every filing). The gateway reads them as data and
publishes them in ``local``, ``test`` and ``sandbox`` with
``python -m app.returns.catalogue_loader``.

    uv run python scripts/export_gateway_catalog.py          # write
    uv run python scripts/export_gateway_catalog.py --check  # exit 1 when stale

Needs no database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.reporting.service.gateway_catalog import exported_documents

CATALOGUE_DIR = (
    Path(__file__).resolve().parents[2] / "gateway" / "api" / "app" / "returns" / "catalogue"
)


def rendered() -> dict[str, str]:
    """``{file name: file text}`` exactly as committed."""
    return {
        name: json.dumps(document, indent=1, ensure_ascii=False) + "\n"
        for name, document in sorted(exported_documents().items())
    }


def stale(target: Path = CATALOGUE_DIR) -> list[str]:
    """File names whose committed text differs from the export (or should not exist)."""
    expected = rendered()
    present: set[str] = (
        {path.name for path in target.glob("*.json")} if target.exists() else set[str]()
    )
    differing = [
        name
        for name, text in expected.items()
        if not (target / name).exists() or (target / name).read_text(encoding="utf-8") != text
    ]
    return sorted({*differing, *(present - expected.keys())})


def write(target: Path = CATALOGUE_DIR) -> int:
    expected = rendered()
    target.mkdir(parents=True, exist_ok=True)
    for path in target.glob("*.json"):
        if path.name not in expected:
            path.unlink()
    for name, text in expected.items():
        (target / name).write_text(text, encoding="utf-8")
    return len(expected)


class _Args(argparse.Namespace):
    check: bool = False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the catalogue is stale")
    args = parser.parse_args(argv, namespace=_Args())
    if args.check:
        names = stale()
        if names:
            print(
                f"the gateway return catalogue is stale ({len(names)} file(s), e.g. {names[0]}); "
                "run: uv run python scripts/export_gateway_catalog.py",
                file=sys.stderr,
            )
            return 1
        print("the gateway return catalogue is fresh")
        return 0
    count = write()
    print(f"wrote {count} return definitions to {CATALOGUE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
