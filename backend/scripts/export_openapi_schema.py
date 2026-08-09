from __future__ import annotations

import json
import os
import sys

# Exporting the schema must not spin up the in-process live-engine worker: its
# background-thread logs (loguru JSON) would interleave with the JSON dumped to stdout
# and corrupt the captured openapi-schema.json. Force it off before importing the app,
# so the export is deterministic regardless of a developer's .env.
os.environ["RUN_INPROCESS_WORKER"] = "0"

from app.main import app  # noqa: E402 - import must follow the worker-disable above


def main() -> int:
    """Write the schema to argv[1] when given, else stdout.

    Writing the file directly (rather than shell-redirecting stdout) keeps
    the schema immune to anything a developer's .env routes to stdout —
    app-creation warnings through a JSON log sink corrupted the redirect
    form (2026-08-09).
    """
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as handle:
            json.dump(app.openapi(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        return 0
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
