"""The real API with a file-controlled reporting clock for commencement journeys.

Only the reporting date dependency changes; routes, persistence, authentication
and eligibility are the product's. No responses are intercepted or replaced.
"""

import json
import os
from datetime import date
from pathlib import Path

from app.main import app
from app.reporting.service import calendar, packages

__all__ = ["app"]

CLOCK = Path(os.environ["E2E_REGULATORY_CLOCK"])
CLOCK.write_text(json.dumps({"today": "2026-12-31"}))


class ReportingDate(date):
    @classmethod
    def today(cls):
        return date.fromisoformat(json.loads(CLOCK.read_text())["today"])


calendar.date = ReportingDate
packages.date = ReportingDate
