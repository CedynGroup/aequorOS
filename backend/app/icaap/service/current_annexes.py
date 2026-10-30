"""Current-annex selection using the caller's managed database session."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.icaap.domain.annexes import AnnexKey, AnnexVersion
from app.reporting.public import RegulatoryPackage


class PostgresCurrentAnnexRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def current(self, key: AnnexKey) -> AnnexVersion | None:
        package = self._db.scalar(
            select(RegulatoryPackage)
            .where(
                RegulatoryPackage.organization_id == key.organization_id,
                RegulatoryPackage.bank_id == key.bank_id,
                RegulatoryPackage.return_code == key.return_code,
                RegulatoryPackage.reporting_date == key.reporting_date,
                RegulatoryPackage.basis == key.basis,
                RegulatoryPackage.status != "superseded",
            )
            .order_by(RegulatoryPackage.version.desc())
            .limit(1)
        )
        if package is None:
            return None
        return AnnexVersion(package.id, package.version, package.content_digest)
