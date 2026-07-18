"""Database-direct source adapter package.

One config-driven adapter over a four-backend driver abstraction (Oracle, SQL
Server, JDBC, ODBC). Importing this package registers the adapter with the
ingestion registry under source system ``DB_DIRECT``.
"""

from app.adapters.database_direct.adapter import DatabaseDirectAdapter

__all__ = ["DatabaseDirectAdapter"]
