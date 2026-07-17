"""Live OFS transport (TAFJ / OFS channel).

This transport builds a real ``ENQUIRY.SELECT`` OFS request for a domain and
would submit it to the bank's OFS endpoint over the authenticated session. The
request construction is complete and tested; the single network submission is
the portal-gated completion point.

Per the data-engine spec §9 we do NOT invent the exact TAFJ endpoint shape or
submission protocol without Temenos developer-portal access, so ``_submit``
raises a classified :class:`TemenosError` (CORE_UNAVAILABLE) until a Temenos-
approved engineer wires the documented endpoint. The framework defaults to
:class:`~app.adapters.temenos_t24.transport.UnavailableTransport`; this class is
selected only when a connection explicitly enables live OFS and the submission
hook is completed. Fixtures exercise the whole pipeline without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.temenos_t24.errors import (
    TemenosError,
    TemenosErrorCode,
    render_bank_facing,
)
from app.adapters.temenos_t24.ofs import build_enquiry_message, parse_ofs_response
from app.adapters.temenos_t24.transport import RawDomainResponse

if TYPE_CHECKING:
    from app.adapters.temenos_t24.auth import TemenosSession
    from app.adapters.temenos_t24.transport import DomainRequest

_NO_CACHE = "not available"


class OfsTransport:
    """Fetches a domain by submitting an OFS enquiry over the session."""

    def __init__(self, *, core_system: str = "your core banking system") -> None:
        self._core_system = core_system

    def fetch(self, session: TemenosSession, request: DomainRequest) -> RawDomainResponse:
        enquiry = request.enquiry or request.application or request.domain
        message = build_enquiry_message(enquiry, request.selection)
        raw = self._submit(session, message, request)
        response = parse_ofs_response(raw)
        if not response.ok:
            raise TemenosError(
                render_bank_facing(
                    TemenosErrorCode.RESPONSE_MALFORMED,
                    core_system=self._core_system,
                    domain=request.domain,
                ),
                internal_detail=(
                    f"OFS enquiry {enquiry!r} returned status {response.error_code!r}: "
                    f"{response.error_text!r}"
                ),
            )
        # The codec already flattened records; the transport re-serializes each
        # record block so the staged bundle stays codec-agnostic.
        records = [message_block for message_block in raw.splitlines() if message_block.strip()]
        return RawDomainResponse(
            domain=request.domain, mode=request.mode, records=records, source=enquiry
        )

    def _submit(self, session: TemenosSession, message: str, request: DomainRequest) -> str:
        """Submit an OFS message over the session and return the raw response.

        Completion point: submit ``message`` to the bank's OFS/TAFJ endpoint
        (``session.endpoint``) with the session token and return the raw OFS
        response text. Pending Temenos portal validation of the endpoint + auth,
        this classifies as CORE_UNAVAILABLE so an attempted live pull records an
        actionable failed batch rather than fabricating a submission protocol.
        """
        raise TemenosError(
            render_bank_facing(
                TemenosErrorCode.CORE_UNAVAILABLE,
                core_system=self._core_system,
                timestamp=_NO_CACHE,
            ),
            internal_detail=(
                "live OFS transport pending Temenos portal validation; would submit enquiry "
                f"{request.enquiry!r} to {session.endpoint!r}"
            ),
        )
