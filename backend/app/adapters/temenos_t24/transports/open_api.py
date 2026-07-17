"""Live Transact Open API / Data Hub transport.

Builds the paginated REST request for a domain against the Transact API gateway
using the session's client-credentials bearer token, and would page through the
published product-API JSON response. The request/pagination construction is
complete; the network call is the portal-gated completion point.

Per the data-engine spec §9 we do not invent the exact gateway host, client-
credentials token endpoint, or product-API pagination envelope without portal
access, so ``_get`` raises a classified CORE_UNAVAILABLE until completed. The
framework defaults to the fixture/unavailable transport; fixtures exercise the
full pipeline offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.adapters.temenos_t24.errors import (
    TemenosError,
    TemenosErrorCode,
    render_bank_facing,
)
from app.adapters.temenos_t24.transport import RawDomainResponse

if TYPE_CHECKING:
    from app.adapters.temenos_t24.auth import TemenosSession
    from app.adapters.temenos_t24.transport import DomainRequest

_NO_CACHE = "not available"


class OpenApiTransport:
    """Fetches a domain via a Transact Open API resource, paging until exhausted."""

    def __init__(self, *, core_system: str = "your core banking system") -> None:
        self._core_system = core_system

    def fetch(self, session: TemenosSession, request: DomainRequest) -> RawDomainResponse:
        url = f"{session.endpoint.rstrip('/')}/{(request.endpoint or request.domain).lstrip('/')}"
        records: list[Any] = []
        page = 0
        while True:
            params = {
                **request.selection,
                "page.size": request.page_size,
                "page.start": page * request.page_size,
            }
            batch = self._get(session, url, params, request)
            if not batch:
                break
            records.extend(batch)
            if len(batch) < request.page_size:
                break
            page += 1
        return RawDomainResponse(
            domain=request.domain, mode=request.mode, records=records, source=url
        )

    def _get(
        self,
        session: TemenosSession,
        url: str,
        params: dict[str, Any],
        request: DomainRequest,
    ) -> list[Any]:
        """GET one page of records and return the JSON ``body`` record list.

        Completion point: issue an authenticated GET to ``url`` with ``params``
        and the client-credentials bearer token, and return the response
        ``body`` array. Pending portal validation, this classifies as
        CORE_UNAVAILABLE.
        """
        raise TemenosError(
            render_bank_facing(
                TemenosErrorCode.CORE_UNAVAILABLE,
                core_system=self._core_system,
                timestamp=_NO_CACHE,
            ),
            internal_detail=(
                "live Open API transport pending Temenos portal validation; would GET "
                f"{url!r} for domain {request.domain!r}"
            ),
        )
