from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    OPENAPI_ERROR_RESPONSES,
    UnhandledExceptionMiddleware,
    register_exception_handlers,
)
from app.core.logging import configure_logging, logger
from app.core.request_id import RequestIdMiddleware
from app.worker import start_inprocess_worker


def generate_operation_id(route: APIRoute) -> str:
    parts = route.name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def _warn_if_signing_unconfigured(settings: Settings) -> None:
    """Log the signing gap at startup. Do NOT refuse to boot.

    An earlier version raised here, and that was disproportionate: on
    2026-07-26 a production deploy with no ``SIGNER_ID_PEPPER`` took down
    liquidity, FX, Basel and treasury — every capability unrelated to filing —
    and left nobody able to sign in and relax the signing policy, which is the
    documented way out. The guard removed the escape hatch it pointed at.

    The property worth keeping is "nobody discovers this at a filing deadline",
    and two narrower mechanisms already provide it: ``/health/ready`` fails, so
    a deploy or an orchestrator's health check surfaces it immediately, and the
    filing path itself refuses via ``ensure_signing_configured`` with a message
    naming the settings. Neither costs anything to a deployment that is not
    filing today.

    With the ``ATTESTATION_ESIGN_REQUIRED`` kill-switch off, the signing gaps
    are irrelevant — no return can demand a signature — so the gap warning is
    replaced by one explicit statement of the suspended requirement.
    """
    attestation = settings.attestation
    if not attestation.esign_required:
        logger.bind(app_env=settings.app.app_env).warning(
            "ATTESTATION_ESIGN_REQUIRED=0: the e-sign requirement is disabled "
            "platform-wide. Every return follows the signature-optional "
            "workflow (maker-checker approval only); configured signing "
            "policies are dormant until the flag is re-enabled."
        )
        return
    gaps = attestation.signing_readiness_gaps()
    if gaps:
        logger.bind(app_env=settings.app.app_env, missing=gaps).warning(
            "Attestation signing is not configured, so no regulatory return can "
            "be certified or filed. Every other capability is unaffected."
        )


def _warn_if_trust_is_unanchored(settings: Settings) -> None:
    """Say out loud when signatures will be verified against their own chain.

    An OpenBao deployment issues every officer certificate from its PKI mount,
    so an institutional root EXISTS — and if nobody points
    ``ATTESTATION_TRUST_ROOTS`` at it, verification silently falls back to the
    chain each signature carries. That still proves the certificate was issued
    by the authority it names; it does not prove the authority is one the
    institution recognises, and the two are easy to confuse from a green report.
    The verifier reports the difference (``trust_anchor: embedded_chain``) and
    this makes sure an operator hears it before an examiner does.

    A warning, not a refusal: unlike a missing signing key this does not stop a
    return being filed, and taking the API down over it would repeat the mistake
    ``_warn_if_signing_unconfigured`` documents. ``scripts/bootstrap_openbao_pki.py``
    writes the anchor file and prints the line to set.
    """
    attestation = settings.attestation
    if attestation.signing_backend != "openbao" or attestation.trust_roots_configured:
        return
    logger.bind(
        pki_mount=attestation.openbao_pki_mount, missing="ATTESTATION_TRUST_ROOTS"
    ).warning(
        "Officer certificates are issued by the OpenBao PKI mount, but "
        "ATTESTATION_TRUST_ROOTS is unset: verification will anchor on the chain "
        "each signature carries rather than on the institution's own root, and "
        "signed PDFs cannot embed long-term validation material. Run "
        "scripts/bootstrap_openbao_pki.py --trust-roots <path> to write the anchor."
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging.log_level)
    _warn_if_signing_unconfigured(settings)
    _warn_if_trust_is_unanchored(settings)

    app = FastAPI(
        title=settings.app.app_name,
        responses=OPENAPI_ERROR_RESPONSES,
        generate_unique_id_function=generate_operation_id,
    )
    # Middleware is applied outside-in in REVERSE registration order, so this
    # reads inside-out: unhandled exceptions are converted to a 500 below the
    # request-id and CORS layers, which is what lets a browser actually see the
    # error instead of a CORS violation (see UnhandledExceptionMiddleware).
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestIdMiddleware)

    if settings.cors.origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.origins,
            allow_credentials=True,
            allow_methods=settings.cors.methods,
            allow_headers=settings.cors.headers,
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api")

    # Optional in-process live-engine worker (off unless RUN_INPROCESS_WORKER).
    start_inprocess_worker()
    return app


app = create_app()
