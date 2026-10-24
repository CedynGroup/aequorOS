"""The real API with a file-controlled reporting clock for commencement journeys.

Only the reporting date dependency changes; routes, persistence, authentication
and eligibility are the product's. No responses are intercepted or replaced.
"""

import json
from datetime import date
from pathlib import Path

from app.main import app
from app.reporting.service import calendar, packages

__all__ = ["app"]

CLOCK = Path(__file__).parents[1] / ".tmp" / "regulatory-clock.json"


class ReportingDate(date):
    @classmethod
    def today(cls):
        return (
            date.fromisoformat(json.loads(CLOCK.read_text())["today"])
            if CLOCK.exists()
            else date.today()
        )


calendar.date = ReportingDate
packages.date = ReportingDate
