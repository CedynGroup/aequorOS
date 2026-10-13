from __future__ import annotations

import json
import os
import sys

# Exporting the schema must not start background jobs. Disable the in-process
# live-engine worker before importing the app, regardless of a developer's .env.
os.environ["RUN_INPROCESS_WORKER"] = "0"

from app.main import app  # noqa: E402 - import must follow the worker-disable above


def main() -> int:
    """Write the schema to argv[1] when given, else stdout.

    Writing the file directly keeps the schema separate from incidental stdout
    output during application imports.
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
