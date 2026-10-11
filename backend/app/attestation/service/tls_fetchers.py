"""Verified transports for issuer certificates and PDF revocation evidence.

Keep pyHanko's parsing, caching and scheduling, replacing only its HTTP seam.
An HTTP certificate/CRL/OCSP URL cannot silently downgrade signing transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import httpx
import requests
from pyhanko_certvalidator.fetchers.api import Fetchers  # type: ignore[reportMissingTypeStubs]
from pyhanko_certvalidator.fetchers.requests_fetchers.cert_fetch_client import (  # type: ignore[reportMissingTypeStubs]
    RequestsCertificateFetcher,
)
from pyhanko_certvalidator.fetchers.requests_fetchers.crl_client import (  # type: ignore[reportMissingTypeStubs]
    RequestsCRLFetcher,
)
from pyhanko_certvalidator.fetchers.requests_fetchers.ocsp_client import (  # type: ignore[reportMissingTypeStubs]
    RequestsOCSPFetcher,
)

from app.core.tls import client_context, require_https


class _VerifiedRequests:
    async def _get(self, url: str, *, acceptable_content_types: Iterable[str]) -> requests.Response:
        return await asyncio.to_thread(
            self._request, "GET", url, headers={"Accept": ",".join(acceptable_content_types)}
        )

    async def _post(
        self, url: str, data: bytes, *, content_type: str, acceptable_content_types: Iterable[str]
    ) -> requests.Response:
        return await asyncio.to_thread(
            self._request,
            "POST",
            url,
            data=data,
            headers={"Accept": ",".join(acceptable_content_types), "Content-Type": content_type},
        )

    @staticmethod
    def _request(
        method: str, url: str, *, headers: dict[str, str], data: bytes | None = None
    ) -> requests.Response:
        require_https(url, field="certificate validation evidence")
        with httpx.Client(
            verify=client_context(),
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        ) as client:
            try:
                fetched = client.request(method, url, headers=headers, content=data)
            except httpx.HTTPError as exc:
                raise requests.RequestException("Certificate evidence transport failed.") from exc
        if fetched.status_code != 200:
            raise requests.RequestException("Certificate evidence endpoint refused the request.")
        # pyHanko's Requests fetchers consume content and headers. Preserve their
        # nominal Response contract while HTTPX owns certificate verification.
        response = requests.Response()
        response.status_code = fetched.status_code
        response.headers.update(fetched.headers)
        response._content = fetched.content  # requests' buffered response construction
        return response


class _CertificateFetcher(_VerifiedRequests, RequestsCertificateFetcher):
    pass


class _CRLFetcher(_VerifiedRequests, RequestsCRLFetcher):
    pass


class _OCSPFetcher(_VerifiedRequests, RequestsOCSPFetcher):
    pass


def verified_fetchers() -> Fetchers:
    return Fetchers(
        cert_fetcher=_CertificateFetcher(), crl_fetcher=_CRLFetcher(), ocsp_fetcher=_OCSPFetcher()
    )
