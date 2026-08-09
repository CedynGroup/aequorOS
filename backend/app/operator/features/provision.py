"""POST /operator/v1/tenants — the onboarding saga endpoint."""

from __future__ import annotations

import logging
from typing import Annotated

import boto3
from botocore.config import Config as BotoConfig
from fastapi import APIRouter, Depends

from app.core.config import get_operator_settings
from app.operator.deps import Operator, OperatorDb
from app.operator.services import tenant_provisioning
from app.operator.services.tenant_provisioning import ProvisioningClients
from app.schemas.operator import ProvisioningResultRead, TenantProvisionCreate
from app.storage.config import StorageRetiredError, enforce_retirement, get_storage_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants", tags=["operator-provisioning"])


def get_provisioning_clients() -> ProvisioningClients:
    """Build the real external clients. Tests override this dependency with
    fakes — the saga itself never talks to the network directly."""
    storage_settings = get_storage_settings()
    s3_client = None
    unavailable_reason: str | None = None
    if not storage_settings.configured:
        unavailable_reason = (
            "object storage is not configured (S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY)"
        )
    else:
        try:
            enforce_retirement(storage_settings)
        except StorageRetiredError as exc:
            unavailable_reason = str(exc)
        else:
            # Same construction the sanctioned storage client uses.
            s3_client = boto3.client(
                "s3",
                endpoint_url=storage_settings.endpoint,
                aws_access_key_id=storage_settings.access_key,
                aws_secret_access_key=storage_settings.secret_key,
                region_name=storage_settings.region,
                config=BotoConfig(
                    s3={
                        "addressing_style": (
                            "path" if storage_settings.force_path_style else "auto"
                        )
                    },
                    retries={"max_attempts": 5, "mode": "adaptive"},
                ),
            )
    kms_client = None
    if get_operator_settings().aws_kms_enabled:
        try:
            kms_client = boto3.client("kms", region_name=storage_settings.region)
        except Exception:  # noqa: BLE001 - saga reports the gap; endpoint must not 500
            logger.exception("failed to construct the KMS client")
    return ProvisioningClients(
        s3_client=s3_client,
        storage_settings=storage_settings,
        kms_client=kms_client,
        unavailable_reason=unavailable_reason,
    )


@router.post("", response_model=ProvisioningResultRead)
def provision_tenant(
    payload: TenantProvisionCreate,
    db: OperatorDb,
    operator: Operator,
    clients: Annotated[ProvisioningClients, Depends(get_provisioning_clients)],
) -> ProvisioningResultRead:
    """Onboard a bank tenant (developer.md §2). Always answers 200 with the
    saga's explicit step results; ``succeeded`` tells the truth and a failed
    saga leaves no half-tenant behind."""
    return tenant_provisioning.provision_tenant(db, operator, payload, clients)
