"""Install connection refusal before a pure-test child imports application code."""

from __future__ import annotations

from scripts.ci_no_database import install

install()
