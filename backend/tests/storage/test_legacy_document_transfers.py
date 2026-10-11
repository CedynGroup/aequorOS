from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from moto import mock_aws

from app.integrations.storage.s3 import S3ObjectStorage


def test_legacy_document_upload_and_download_signing_remain_available() -> None:
    with mock_aws():
        storage = S3ObjectStorage(
            region_name="us-east-1",
            endpoint_url=None,
            access_key_id="synthetic-access",
            secret_access_key="synthetic-secret",
            force_path_style=True,
        )
        upload = storage.create_presigned_upload_url(
            bucket="synthetic-documents",
            object_key="orgs/synthetic/original.pdf",
            content_type="application/pdf",
            expires_seconds=120,
        )
        assert upload.method == "PUT"
        assert upload.headers == {"Content-Type": "application/pdf"}
        assert upload.expires_in_seconds == 120
        assert urlsplit(upload.url).path.endswith("/orgs/synthetic/original.pdf")
        assert parse_qs(urlsplit(upload.url).query)["X-Amz-Expires"] == ["120"]
        download = storage.create_presigned_download_url(
            bucket="synthetic-documents",
            object_key="orgs/synthetic/original.pdf",
            expires_seconds=60,
        )
        assert urlsplit(download).path.endswith("/orgs/synthetic/original.pdf")
        assert parse_qs(urlsplit(download).query)["X-Amz-Expires"] == ["60"]
        assert (
            parse_qs(urlsplit(upload.url).query)["X-Amz-Signature"]
            != parse_qs(urlsplit(download).query)["X-Amz-Signature"]
        )
