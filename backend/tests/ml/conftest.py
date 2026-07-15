"""ML test configuration: force the reduced model config and isolate artifacts.

Environment variables are set at conftest import time (pytest imports all
conftests during collection, before any test runs) so no test can accidentally
train the full-size model or write into a real artifacts directory.
``CASHFLOW_ARTIFACTS_DIR`` is always overridden — a developer's local value
must never leak into the suite; ``CASHFLOW_FAST_TEST`` honors an explicit
outer value (the suite is normally run with ``CASHFLOW_FAST_TEST=1``).
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("CASHFLOW_FAST_TEST", "1")
os.environ["CASHFLOW_ARTIFACTS_DIR"] = tempfile.mkdtemp(prefix="cashflow-ml-artifacts-")
