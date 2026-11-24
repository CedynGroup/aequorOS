"""Bootstrap the return registry: the AequorOS-aligned catalogue, through the real workflow."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.context import AppContext
from app.returns.catalogue_loader import publish_catalogue
from app.seed.staff import sign_in


def bootstrap_registry(
    session: Session, ctx: AppContext, at: datetime, codes: Iterable[str] | None = None
) -> list[str]:
    """Publish every aligned definition (or only ``codes``): drafted by a returns policy
    officer, approved by a second policy officer and a senior supervisor."""
    drafter = sign_in(session, ctx, "easante")
    approvers = (sign_in(session, ctx, "madjei"), sign_in(session, ctx, "kowusu"))
    return publish_catalogue(session, ctx, drafter=drafter, approvers=approvers, at=at, codes=codes)
