"""Identity's interface for other features: re-exports only, no logic."""

from __future__ import annotations

from app.identity.models.bank import Bank
from app.identity.models.user import User
from app.identity.service.scoped_authorization import require_resolved_bank_permission, resolve_bank

__all__ = ["Bank", "User", "require_resolved_bank_permission", "resolve_bank"]
