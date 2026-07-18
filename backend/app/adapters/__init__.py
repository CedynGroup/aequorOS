"""Source adapters: the only code in AequorOS that knows source systems.

Each adapter package is independently versioned and testable; adapters never
share translation code with each other, and nothing outside this package may
import source-system specifics. See ``app.domain.ingestion.adapter`` for the
contract every adapter implements.

Importing this package registers every shipped adapter with the registry.
"""

from app.adapters.api_push.adapter import ApiPushAdapter
from app.adapters.database_direct.adapter import DatabaseDirectAdapter
from app.adapters.excel_csv.adapter import ExcelCsvAdapter
from app.adapters.temenos_t24.adapter import TemenosT24Adapter

__all__ = [
    "ApiPushAdapter",
    "DatabaseDirectAdapter",
    "ExcelCsvAdapter",
    "TemenosT24Adapter",
]
