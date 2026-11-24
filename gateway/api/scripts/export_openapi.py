# pyright: standard
"""Write the OpenAPI contract documents to gateway/contract/ (or check they are fresh).

uv run python scripts/export_openapi.py          # write
uv run python scripts/export_openapi.py --check  # exit 1 if a committed export is stale
"""

from __future__ import annotations

import sys

from app.openapi_export import CONTRACT_DIR, exported


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for name, text in exported().items():
        path = CONTRACT_DIR / name
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
            print(f"wrote {path}")
    if stale:
        print(
            "stale OpenAPI exports: " + ", ".join(stale) + " (run scripts/export_openapi.py)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
