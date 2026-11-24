"""Notifications's interface for other features: re-exports only, no logic.

A producer needs four things: the catalogue (which event), the message parts
(what to say), ``publish`` (who to tell), and — for a scheduled producer — the
organisation's reminder settings.
"""

from app.notifications import catalog
from app.notifications.rendering import Action, Fact, Message
from app.notifications.service import notifications
from app.notifications.service.preferences import OrgDelivery, org_delivery
from app.notifications.service.publishing import publish

__all__ = [
    "Action",
    "Fact",
    "Message",
    "OrgDelivery",
    "catalog",
    "notifications",
    "org_delivery",
    "publish",
]
