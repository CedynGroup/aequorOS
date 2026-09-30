"""Typed failures of the BI query path, each with the HTTP shape it maps to.

Every failure the compiler or the execution wrapper can raise is one of these.
Their messages are production copy for the caller — they name a member id, a
count or a limit, and NEVER the SQL that was (or would have been) run, because
the query text encodes the mart layout and that is not a bank-facing surface.
Nor do they reflect client text, in EITHER of the two places an id travels: the
``message`` and the machine-readable ``members`` list both carry a member id
only when it is shaped like one (:func:`is_member_id`), and a filter VALUE is
never echoed — the message names the type the column expects instead. A
malformed id is withheld from both, because the API layer serialises `members`
straight into the error body and the caller already knows what it sent (audit
A6-03: the message withheld it and `members` did not). The API layer maps
``status_code`` / ``code`` onto the error envelope; pydantic validation of the
request body itself is a 422 before any of this runs, and it bounds a member id
at ``MEMBER_ID_MAX_LENGTH`` so the long-id case mostly never reaches here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.bi.catalogue import UnknownMember as CatalogueUnknownMember
from app.schemas.bi import MEMBER_ID_MAX_LENGTH

#: What a catalogue id looks like; anything else is withheld from messages.
_MEMBER_ID_SHAPE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")


def is_member_id(member_id: str) -> bool:
    """Whether a string is shaped like a catalogue member id, and short enough.

    The length bound is the schema's own (``MEMBER_ID_MAX_LENGTH``) rather than a
    second number, so what the API accepts and what an error will repeat back
    cannot drift apart.
    """
    return len(member_id) <= MEMBER_ID_MAX_LENGTH and _MEMBER_ID_SHAPE.match(member_id) is not None


def member_id_for_display(member_id: str) -> str:
    """The id itself when it is shaped like a catalogue id, else a placeholder."""
    return member_id if is_member_id(member_id) else "(id withheld: not a well-formed member id)"


class BiQueryError(Exception):
    """Base of every query-path failure. 422-shaped unless a subclass says otherwise."""

    status_code: int = 422
    code: str = "bi_invalid_query"

    def __init__(self, message: str, *, members: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        #: The member ids the failure is about, for the error envelope.
        self.members: tuple[str, ...] = tuple(members)

    def __str__(self) -> str:
        return self.message


class UnknownMember(BiQueryError, CatalogueUnknownMember):
    """A member id the catalogue does not know, or one whose column is not mapped.

    Raised before any authorization decision, so the ONLY thing a caller can
    learn by probing is whether an id exists in the catalogue — which the
    catalogue endpoint tells them anyway. Subclasses the catalogue's own
    ``UnknownMember`` so ``except`` clauses written against either work.
    """

    code = "bi_unknown_member"

    def __init__(self, member_id: str) -> None:
        BiQueryError.__init__(
            self,
            f"Unknown BI member: {member_id_for_display(member_id)}.",
            # Only a well-formed id goes into ``members``. That list is
            # serialised into the response body, so echoing arbitrary client
            # bytes there would contradict this module's stated property for the
            # sake of telling a caller something it already knows. A legitimate
            # typo IS still named, which is the whole value of the field.
            members=(member_id,) if is_member_id(member_id) else (),
        )
        #: The raw id, for internal use only — never serialised.
        self.member_id = member_id


class InvalidQuery(BiQueryError):
    """A well-formed request the compiler refuses: an operator that does not fit
    the column, a value that does not coerce, a member outside the sort
    whitelist, a pivot wider than the cap, measures that span two facts."""

    code = "bi_invalid_query"


class BiQueryTimeout(BiQueryError):
    """The database cancelled the statement at the server-side ``statement_timeout``.

    504-shaped: the request was valid and authorized, the data plane could not
    answer it in time. The message names the budget, never the statement.
    """

    status_code = 504
    code = "bi_query_timeout"

    def __init__(self, timeout_ms: int) -> None:
        super().__init__(
            f"The query exceeded its {timeout_ms} ms budget and was cancelled. "
            "Narrow the time range or add a filter."
        )
        self.timeout_ms = timeout_ms
