# AequorOS Storage Layer

## Implementation Specification

**Status:** Draft v1.0
**Owner:** Dela Anthonio (CTO), Eric Inkoom Danso (CEO)
**Audience:** Engineering. Written to be implementable by an AI coding assistant (Claude Fable 5) under human review, and by human engineers directly.
**Companion document:** `data_engine.md` (this spec references and depends on it throughout)
**Purpose:** Define the storage abstraction layer for the AequorOS Data Engine, covering the object-storage substrate across three backing stores (MinIO for MVP, Google Cloud Storage and Amazon S3 for production), the bucket-per-institution-per-tier pattern, the storage client API, lifecycle rules, encryption posture, and audit integration.

---

## 1. Context and Scope

### 1.1 Why This Document Exists

The AequorOS Data Engine is defined in `data_engine.md`. It specifies what data flows through the system: adapters extract from source systems, canonicalize into the AequorOS model, validate, enrich, and feed the calculation engines. Every layer produces artifacts. Raw source snapshots, canonical records, validation reports, calculation outputs, audit logs, and lineage graphs all need durable, versioned, region-appropriate storage that is secure, auditable, and defensible to a bank IT department during vendor risk assessment.

This document specifies the storage substrate underneath the Data Engine. It defines:

- The bucket-per-institution-per-tier pattern (section 3)
- The storage client abstraction that shields application code from backing store specifics (section 4)
- Backing store implementations for MinIO (MVP), Google Cloud Storage, and Amazon S3 (section 5)
- Lifecycle rules by tier (section 6)
- Encryption and key management (section 7)
- Access control and identity (section 8)
- Audit integration and lineage (section 9)
- Multi-region and data residency handling (section 10)
- Migration path from MinIO to managed cloud (section 11)
- Phasing and milestones (section 12)

### 1.2 What This Document Does Not Specify

- **Infrastructure provisioning** (Terraform modules, Kubernetes manifests, network topology, VPC design). Those live in `/infrastructure/README.md`.
- **Application-layer identity and RBAC** (user roles, session management, UI-level permissions). Those live in `/security/access_control.md`.
- **The canonical data model, adapter framework, validation, enrichment, and calculation engine specifications.** Those live in `data_engine.md`.
- **Cost modeling for storage.** Storage cost is a real concern but is not an architectural constraint at MVP scale. Revisit at Phase 3 when customer count and data volume make it material.

### 1.3 Relationship to Data Engine

Every artifact produced by the Data Engine (per `data_engine.md`) is persisted through the storage layer. The mapping:

| Data Engine Layer | Artifacts | Storage Tier |
|---|---|---|
| Layer 1: Source Adapters | Raw source extracts (T24 exports, Excel files, API responses) | `raw` |
| Layer 2: Canonical Model | Canonical snapshots by as-of-date | `canonical` |
| Layer 3: Validation | Validation reports (per section 6.4 of `data_engine.md`) | `outputs` |
| Layer 4: Enrichment | Enrichment intermediate artifacts, model versions | `canonical` |
| Layer 5: Analytical Store | Regulatory reports, calculation results, dashboards | `outputs` |
| Layer 6: Metadata, Lineage, Audit | Lineage graph exports, audit log archives | `outputs` |
| Working / Ingestion transient | ETL scratch space, intermediate transforms | `temp` |

Every write to storage carries the mandatory metadata columns from section 4.3 of `data_engine.md`. Object paths encode institution_id, tier, entity_type, and as_of_date so that lineage back to source is derivable from the storage path itself.

---

## 2. Non-Negotiable Design Principles

These principles govern every implementation decision. They inherit from and extend the principles in `data_engine.md` section 2.

1. **The storage backend is a configuration choice, not a code choice.** Application code depends on the `StorageClient` interface (section 4). Swapping MinIO for GCS for S3 is a config change plus optional per-backend authentication wiring, never a code rewrite.

2. **Bucket-per-institution-per-tier is the only sanctioned production topology.** Shared-bucket multi-tenancy is prohibited for production data. MVP on MinIO uses the same pattern in miniature. Rationale: bank auditor comfort, data residency, per-institution key management, cleaner IAM (see section 3.2).

3. **Every object is immutable at rest.** Corrections produce new versions, never overwrites. Versioning is mandatory on every bucket. Delete operations are logical (marker), not physical, for retention-covered tiers.

4. **Every object carries provenance metadata.** Custom metadata on the object records: source Data Engine batch ID, ingesting adapter, as-of-date, institution ID, tier, canonical schema version, encryption key ID, and lineage graph node ID (per section 8.2 of `data_engine.md`).

5. **Encryption is customer-managed per institution.** One KMS key (or MinIO KES key) per institution. Rotation is scheduled. Revocation is possible and severs data access without data destruction.

6. **Access is scoped at the IAM layer, not the application layer.** Application bugs cannot cross-tenant leak because the service credentials themselves do not have cross-tenant access.

7. **Access is logged at the storage layer.** Every read and write is captured in access logs that are themselves stored in a separate audit bucket with hash-chained integrity, retained for at least 7 years to satisfy banking examination requirements.

8. **Data residency is per-institution.** Ghanaian bank buckets live in a region satisfying Ghanaian data residency expectations. Buckets are not moved across regions after creation; new institutions get new region-appropriate buckets from provisioning.

9. **No production banking data on self-hosted MinIO.** MinIO is authorized for MVP against synthetic data (per the reference data package: `sample_bank_data/`) and for development environments only. Before the first paying bank customer's data lands, storage migrates to a managed cloud object store (GCS or S3). Rationale in section 5.1.

10. **The storage layer is separately deployable, testable, and observable.** It has its own health checks, metrics, alerting, and integration test suite independent of the Data Engine that uses it.

---

## 3. The Bucket-per-Institution-per-Tier Pattern

### 3.1 Bucket Structure

Each institution provisioned into AequorOS gets a dedicated set of buckets, one per data tier. There is no cross-institution shared storage for regulated data. A separate shared bucket exists for non-sensitive reference data (market data feeds, published regulatory templates, currency codes).

#### 3.1.1 Tiers

Four tiers per institution:

**`raw`** — Raw source extracts as produced by the source system before any AequorOS transformation. T24 API responses, Excel file uploads, CSV drops, database exports. Retained per regulatory requirement (7+ years for banking data). Strictly access-controlled. Immutable after write.

**`canonical`** — Canonical model snapshots after adapter translation. One canonical snapshot per as-of-date per institution, produced by the Data Engine layers 1-4 (per `data_engine.md`). Retained 7+ years. Versioned. Every restatement produces a new snapshot; old snapshots remain retrievable for point-in-time reproduction of past regulatory submissions.

**`outputs`** — Regulatory reports (BSD 1-4 for BoG, equivalent for other regulators), calculation results, dashboards, exported audit trail artifacts. This is the tier bank users read from through the AequorOS UI. Retained 7+ years for regulatory examination support.

**`temp`** — Working storage during processing. Intermediate transforms, in-flight validation artifacts, ETL scratch space. Aggressive lifecycle (delete after 30 days). Not covered by long retention.

#### 3.1.2 Naming Convention

```
aequoros-{env}-{institution_id}-{tier}
```

Where:
- `env` ∈ `{prod, staging, dev, mvp}`
- `institution_id` is the AequorOS-assigned identifier (lowercase, hyphens; e.g., `sbl-gh-001`)
- `tier` ∈ `{raw, canonical, outputs, temp}`

Examples:
- `aequoros-prod-sbl-gh-001-raw`
- `aequoros-prod-sbl-gh-001-canonical`
- `aequoros-prod-sbl-gh-001-outputs`
- `aequoros-prod-sbl-gh-001-temp`
- `aequoros-mvp-sbl-gh-001-canonical` (MVP against synthetic data)

Bucket names are DNS-safe (lowercase, hyphens, 3-63 characters per the strictest of the three backend rules). Do not use underscores, uppercase, dots, or slashes in bucket names.

#### 3.1.3 Shared Reference Bucket

A single shared bucket per environment exists for non-institution-specific data:

```
aequoros-{env}-reference
```

Contents: published market data feeds (BoG rate curves, FX reference rates), public regulatory templates, currency/country reference tables, product taxonomy defaults. Read access is broad across the platform; write access is restricted to a dedicated service account for market data ingestion.

The reference bucket does not contain any institution-derived data.

### 3.2 Why This Pattern

Three concrete reasons this beats shared-bucket multi-tenancy for AequorOS specifically:

**Bank auditor comfort.** When a bank IT auditor asks "where is our data stored," the answer is "in dedicated buckets scoped exclusively to your institution ID, physically separated at the storage layer from every other institution." Auditors accept this. The equivalent for shared-bucket architecture is "in a shared bucket with logical isolation enforced by application code," which invites a much deeper audit.

**Regulatory data lifecycle.** Different tiers have different retention requirements. Raw ingested data may need 7+ years for BoG examination. Working temp data can be deleted quickly. Per-tier buckets let lifecycle rules apply cleanly at the bucket level without complex per-object logic. This is important because per-object lifecycle logic is a common source of retention bugs.

**Data residency at scale.** As AequorOS expands from Ghana to Nigeria, Kenya, and South Africa, each institution's buckets can live in the region appropriate to its regulatory data residency requirements. A Kenyan bank's buckets in a Nairobi-adjacent region, a Ghanaian bank's in a region satisfying BoG expectations. A single shared bucket cannot straddle regions this way.

The trade-off is operational overhead: bucket count grows with customer count. This is mitigated by automated provisioning (section 3.3) and is a well-understood cost of the pattern.

### 3.3 Provisioning at Institution Onboarding

When a new institution is provisioned in AequorOS (which is part of the onboarding process defined in `/product/onboarding_playbook.md`), the storage provisioning is a single automated step. It creates:

1. Four buckets per the naming convention (raw, canonical, outputs, temp)
2. IAM policies scoping access to the institution's service account only
3. Lifecycle rules per tier (section 6)
4. Encryption configuration with a dedicated KMS key for the institution (section 7)
5. Versioning enabled on raw, canonical, and outputs (not temp)
6. Access logging enabled, writing to the platform-wide audit bucket
7. Bucket policy blocking public access
8. Region assignment per the institution's data residency requirement

The provisioning module is a first-class artifact, versioned, tested, and reproducible. Manual bucket creation in a cloud console is prohibited outside of debugging environments; production buckets are provisioned only through the provisioning module.

Provisioning module implementation lives in `/infrastructure/provisioning/`. Terraform is the sanctioned tool for production cloud (GCS and S3). For MinIO in MVP, a Python provisioning script using the MinIO admin API is sufficient.

### 3.4 Object Path Conventions

Within each bucket, objects follow a structured path convention that encodes lineage:

**For `canonical` tier:**
```
{entity_type}/{as_of_date}/{ingestion_batch_id}/{filename}
```
Example: `positions/2026-04-30/b-2026-05-21-1a2b3c/loans.parquet`

**For `raw` tier:**
```
{source_system}/{extraction_date}/{extraction_batch_id}/{original_filename}
```
Example: `t24/2026-04-30/e-2026-05-21-9f8e7d/AA_ARRANGEMENT_export.csv`

**For `outputs` tier:**
```
{output_type}/{as_of_date}/{report_id}/{filename}
```
Example: `bog_returns/2026-04-30/rpt-bsd2-001/BSD2_capital_adequacy.xlsx`

**For `temp` tier:**
```
{job_id}/{step}/{filename}
```
Example: `job-2026-05-21-abc123/step-3-validate/intermediate.parquet`

Paths are stable, predictable, and derivable from Data Engine metadata. Given a canonical snapshot's `institution_id`, `as_of_date`, `entity_type`, and `ingestion_batch_id`, the exact object path is deterministic. This is essential for lineage: the storage location is the lineage endpoint.

---

## 4. Storage Client Abstraction

### 4.1 Why an Abstraction

Application code, and specifically the Data Engine, must not depend on any specific backing store. A single `StorageClient` interface abstracts MinIO, GCS, and S3 behind a common contract. Backend selection is a config decision made at initialization, never in application code.

This is the single most important architectural piece of the storage layer. Getting it right makes the eventual migration from MinIO to managed cloud (section 11) a config change. Getting it wrong forces a rewrite.

### 4.2 The `StorageClient` Interface

Expressed in Python for clarity. Final implementation in the chosen backend language (per `data_engine.md`, Python is the reference language for adapters and Data Engine code).

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Iterator, Optional
from uuid import UUID


@dataclass(frozen=True)
class StorageLocation:
    """Fully qualified location of an object in AequorOS storage."""
    institution_id: str
    tier: str  # 'raw' | 'canonical' | 'outputs' | 'temp'
    object_path: str  # relative path within the bucket, per section 3.4

    @property
    def bucket_name(self) -> str:
        # Resolved by StorageClient using its env config
        raise NotImplementedError  # implemented by concrete client


@dataclass(frozen=True)
class ObjectMetadata:
    """Custom metadata attached to every object at write time."""
    institution_id: str
    tier: str
    as_of_date: Optional[str]  # ISO date string, if applicable
    ingestion_batch_id: Optional[str]
    lineage_node_id: Optional[str]  # from data_engine.md section 8.2
    schema_version: Optional[str]  # canonical schema version for canonical tier
    source_system: Optional[str]
    source_reference: Optional[str]
    kms_key_id: Optional[str]  # the KMS key used for encryption
    checksum_sha256: str  # required
    written_at: datetime
    written_by: str  # service account or user identifier


@dataclass(frozen=True)
class StorageObject:
    """A stored object retrieved from storage."""
    location: StorageLocation
    metadata: ObjectMetadata
    size_bytes: int
    version_id: Optional[str]
    created_at: datetime
    content_type: str


class StorageClient(ABC):
    """
    The single sanctioned interface for reading and writing to AequorOS storage.
    Application code depends on this ABC, not on any concrete backend.

    Concrete implementations exist for MinIO, GCS, and S3.
    """

    @abstractmethod
    def write(
        self,
        location: StorageLocation,
        data: BinaryIO,
        metadata: ObjectMetadata,
        content_type: str = "application/octet-stream",
    ) -> StorageObject:
        """
        Write an object to storage.

        Semantics:
        - Object is encrypted at rest with the institution's KMS key.
        - Metadata is attached as custom object metadata.
        - Access log entry is written.
        - Returns the persisted StorageObject with version_id populated.
        - Idempotent for identical (location, checksum): re-writing the same
          content to the same location is a no-op returning the existing object.

        Raises:
        - StorageAccessError: if IAM does not permit the write.
        - StorageValidationError: if metadata is incomplete or invalid.
        - StorageBackendError: for backend-level failures (retries applied first).
        """

    @abstractmethod
    def read(
        self,
        location: StorageLocation,
        version_id: Optional[str] = None,
    ) -> tuple[StorageObject, BinaryIO]:
        """
        Read an object from storage.

        Semantics:
        - Decrypts using the institution's KMS key.
        - Access log entry is written.
        - version_id=None returns the latest version.
        - Returns (StorageObject, BinaryIO stream).

        Raises:
        - StorageNotFoundError: if object does not exist.
        - StorageAccessError: if IAM does not permit the read.
        """

    @abstractmethod
    def exists(self, location: StorageLocation) -> bool:
        """Check whether an object exists at the location. Does not read content."""

    @abstractmethod
    def list(
        self,
        institution_id: str,
        tier: str,
        prefix: str = "",
        limit: Optional[int] = None,
    ) -> Iterator[StorageObject]:
        """
        List objects under a prefix within an institution's tier.

        Semantics:
        - Returns StorageObject records without opening streams.
        - Access log entry is written per invocation, not per result.
        - limit=None yields all matching; pagination handled internally.
        """

    @abstractmethod
    def list_versions(
        self,
        location: StorageLocation,
    ) -> Iterator[StorageObject]:
        """
        List all versions of an object at a location.
        Used for point-in-time reproduction of past submissions.
        """

    @abstractmethod
    def delete(
        self,
        location: StorageLocation,
    ) -> None:
        """
        Logically delete an object (delete marker; content retained per lifecycle).

        For 'temp' tier: physical delete permitted.
        For 'raw', 'canonical', 'outputs' tiers: logical delete only.
        Access log entry is written.

        Raises:
        - StorageAccessError: if IAM does not permit deletion of this tier.
        """

    @abstractmethod
    def get_metadata(
        self,
        location: StorageLocation,
        version_id: Optional[str] = None,
    ) -> ObjectMetadata:
        """
        Read custom metadata without reading object content.
        Useful for lineage lookups and audit.
        """

    @abstractmethod
    def presigned_url(
        self,
        location: StorageLocation,
        operation: str,  # 'read' | 'write'
        expires_in_seconds: int,
    ) -> str:
        """
        Generate a presigned URL for direct client access.

        Semantics:
        - Used for large file uploads (bank uploads a T24 export via UI).
        - Expiration must be short (default 900s = 15 min).
        - Access log records the URL generation, not each access via the URL.
        - Presigned URLs still respect KMS encryption on the backend.
        """

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Runtime health check for this backend instance."""
```

### 4.3 Errors

A small hierarchy of exceptions, defined once and raised by every implementation:

```python
class StorageError(Exception):
    """Base for all storage layer errors."""


class StorageNotFoundError(StorageError):
    """Object does not exist at the requested location/version."""


class StorageAccessError(StorageError):
    """IAM or authentication denied the operation."""


class StorageValidationError(StorageError):
    """Metadata or content failed pre-flight validation."""


class StorageBackendError(StorageError):
    """
    Backend-level failure. Includes retry-exhausted transient failures,
    quota limits, and other backend-specific issues.
    """
```

### 4.4 Configuration

The StorageClient is instantiated once per process from environment configuration:

```yaml
# storage.yaml (loaded at service startup)
storage:
  backend: "minio"  # 'minio' | 'gcs' | 's3'
  env: "mvp"       # 'prod' | 'staging' | 'dev' | 'mvp'

  # MinIO-specific
  minio:
    endpoint: "https://minio.internal.aequoros.dev:9000"
    access_key_ref: "vault://aequoros/minio/access_key"
    secret_key_ref: "vault://aequoros/minio/secret_key"
    secure: true

  # GCS-specific (activated when backend='gcs')
  gcs:
    project_id: "aequoros-prod"
    credentials_ref: "vault://aequoros/gcs/service_account_json"
    default_region: "europe-west1"
    kms_project_id: "aequoros-security"

  # S3-specific (activated when backend='s3')
  s3:
    region: "af-south-1"
    role_arn: "arn:aws:iam::123456789012:role/aequoros-storage-service"
    kms_region: "af-south-1"

  # Common
  retry:
    max_attempts: 5
    initial_backoff_seconds: 1
    max_backoff_seconds: 60

  access_logging:
    audit_bucket: "aequoros-{env}-audit-logs"
    log_format: "json"
```

Configuration is loaded once. Backend selection is not runtime-dynamic within a process. Reconfiguration requires service restart.

### 4.5 Testing the Abstraction

Every backend implementation ships with:

- **Contract tests.** Verify the backend correctly implements the `StorageClient` interface. Same test suite runs against all three backends. If a test fails on MinIO but passes on GCS, the abstraction is leaking backend semantics; fix it.
- **Idempotency tests.** Confirm that repeated writes of identical content to the same location are true no-ops.
- **Encryption tests.** Confirm that objects written are encrypted at rest and cannot be read without the correct KMS key.
- **IAM tests.** Confirm that a client scoped to institution A cannot read institution B's buckets.
- **Lifecycle tests.** Confirm that temp objects age out per rule.
- **Access log tests.** Confirm that every operation produces an audit log entry.

Contract tests are the single most important part of the abstraction. Fable 5 and any future engineer must run contract tests against every backend implementation before shipping.

---

## 5. Backing Store Implementations

Three concrete implementations of `StorageClient`. Each satisfies the interface in section 4.

### 5.1 A Note on Backing Store Selection

**MVP: MinIO.** Self-hosted MinIO is sanctioned for MVP against synthetic data only. Rationale: Eric already runs MinIO, the S3-compatible API means the abstraction can be developed and tested against it now, and there is no real customer data at risk. Ship faster.

**Production: managed cloud object storage.** Before the first paying bank customer's real data lands, storage migrates to a managed cloud object store. The primary reason is not that MinIO is technically inferior (it isn't; well-operated MinIO is secure), but that self-hosted infrastructure fails a bank vendor risk assessment on the *ecosystem* around the storage: SOC 2 attestation, physical security, HSM-backed KMS, multi-region replication, DDoS mitigation, 24/7 monitoring, and dozens of other things that inherit trivially from AWS or GCP but require dedicated infrastructure operations investment to replicate on self-hosted MinIO. For a seed-stage company, that investment is the wrong trade-off. Product engineering time goes into the moat (calculation engines, T24 integration, regulatory templates), not into operating a private bank-grade cloud.

**Between GCS and S3, GCS is the preferred production backend.** Rationale: the rest of the AequorOS stack (Vertex AI, BigQuery, Document AI) is on GCP. Cross-cloud (GCP compute + AWS storage) adds egress costs, IAM federation complexity, and split-cloud operational overhead that doesn't buy anything meaningful. Both are supported in this spec because bank customers may in some cases require S3 specifically (rare but not impossible; typically driven by existing AWS-based bank IT preferences).

### 5.2 MinIO Implementation

**Purpose:** MVP against synthetic reference data (`sample_bank_data/`). Development environments. Local testing.

**Not sanctioned for:** Any real bank data. Any production environment carrying real regulatory data.

**Key implementation notes:**

- Uses the MinIO Python SDK (`minio-py`).
- Bucket-per-institution-per-tier pattern applies unchanged (MinIO supports many buckets per deployment).
- Object versioning enabled per bucket.
- Server-side encryption via MinIO KES (Key Encryption Service) if available; if KES is not deployed, SSE-C with client-supplied keys per institution is the fallback. Client-side encryption is not sanctioned because it complicates key management.
- Lifecycle rules for `temp` tier via MinIO ILM (Information Lifecycle Management).
- Access logging via MinIO audit webhooks writing to the audit bucket.
- Bucket policies scope access; per-institution IAM users each with credentials for their specific buckets.

**Provisioning:**

A Python script `provisioning/minio_provisioning.py` uses the MinIO admin API to create buckets, apply policies, enable versioning, configure lifecycle rules, and register KES key mappings. Idempotent: re-running against an already-provisioned institution is a no-op.

**Deployment posture:**

For MVP purposes, Eric's existing self-hosted MinIO is sufficient. If MinIO is used for a shared development environment across Eric and Dela, single-node MinIO is fine. Multi-node deployment is not required for MVP.

**Explicit deprecation path:**

Every MinIO-backed environment carries an explicit `retire_after` date in its config. When `env=mvp`, this date is fixed at 6 months from initial deployment (or before the first bank customer signs an LOI, whichever is sooner). Beyond `retire_after`, the environment is decommissioned or migrated to a managed cloud backend. This is enforced by a startup check: if `retire_after` has passed and `env=mvp`, the storage client refuses to initialize.

### 5.3 Google Cloud Storage Implementation

**Purpose:** Production storage for AequorOS deployments on GCP. Preferred production backend.

**Key implementation notes:**

- Uses the `google-cloud-storage` Python client library.
- Buckets are regional (not multi-region) so that data residency can be enforced per-institution.
- Object versioning enabled per bucket.
- Server-side encryption via CMEK (Customer-Managed Encryption Keys) with keys stored in Cloud KMS. One CryptoKey per institution.
- Lifecycle rules configured per bucket at provisioning time.
- Access logging via Cloud Audit Logs with Data Access logs enabled on storage buckets; logs sink to the audit bucket.
- Access control via IAM: each institution has a service account with permissions scoped to its own buckets. Application code impersonates the correct service account per request.
- Uniform bucket-level access enforced (no ACLs; IAM only).
- Public access prevention enforced.

**Region selection:**

| Institution country | GCS region |
|---|---|
| Ghana | `europe-west1` (Belgium) or `europe-west4` (Netherlands) — no GCP Ghana region as of Phase 2; validate current expectations with BoG for cross-border banking data |
| Nigeria | `europe-west1` or migrate to `africa-south1` when Johannesburg region matures |
| Kenya | `europe-west1` |
| South Africa | `africa-south1` (Johannesburg) |
| Egypt | `europe-west1` |

Region selection is per-institution at provisioning and immutable thereafter.

**Provisioning:**

A Terraform module `provisioning/gcs/institution.tf` creates buckets, KMS keys, IAM bindings, lifecycle rules, and audit log sinks in one apply. The module takes `institution_id`, `region`, and `env` as inputs. Terraform state is stored in a separate secure state bucket.

**Cross-project considerations:**

Storage buckets live in the AequorOS production project (`aequoros-prod`). KMS keys live in a separate security project (`aequoros-security`) with strict IAM. Audit log sinks land in a dedicated logging project (`aequoros-audit`). This separation limits blast radius: a compromise of the compute project does not compromise the keys.

### 5.4 Amazon S3 Implementation

**Purpose:** Alternative production backend for deployments where bank customer preference or specific compliance requirements mandate AWS.

**Key implementation notes:**

- Uses `boto3` Python SDK.
- Bucket-per-institution-per-tier applied unchanged; note S3's global bucket namespace, so bucket names include unique suffixes if collision is possible.
- Object versioning enabled per bucket.
- Server-side encryption via SSE-KMS with customer-managed KMS keys (CMK). One KMS key per institution.
- Lifecycle rules configured per bucket at provisioning.
- Access logging via S3 Server Access Logging plus CloudTrail Data Events; logs to the audit bucket.
- Access control via IAM: each institution has an IAM role scoped to its own buckets. Application code assumes the correct role per request via STS.
- Block Public Access enabled at the account and bucket level.
- S3 Object Lock in compliance mode for `raw`, `canonical`, and `outputs` tiers to enforce write-once-read-many semantics.

**Region selection:**

| Institution country | S3 region |
|---|---|
| Ghana | `af-south-1` (Cape Town) — closest to Ghana; validate with BoG for cross-border banking data |
| Nigeria | `af-south-1` |
| Kenya | `af-south-1` or `eu-west-1` |
| South Africa | `af-south-1` |
| Egypt | `eu-west-1` or `me-south-1` |

Region selection is per-institution at provisioning and immutable thereafter.

**Provisioning:**

A Terraform module `provisioning/s3/institution.tf` mirrors the GCS module: buckets, KMS keys, IAM roles, lifecycle rules, and audit log configuration in one apply.

**Cross-account considerations:**

Buckets live in the AequorOS production AWS account. KMS keys live in a separate security account with strict IAM. Audit logs land in a dedicated logging account. Same separation-of-concerns principle as GCS section 5.3.

### 5.5 Feature Parity Across Backends

The three implementations must produce indistinguishable behavior from the `StorageClient` interface's perspective. Any backend-specific behavior (e.g., S3's Object Lock, GCS's uniform bucket-level access) is implemented within the concrete backend and does not leak through the interface.

A single feature parity matrix, maintained in `/storage/parity_matrix.md`, tracks known behavioral differences and mitigations. Examples of differences to be aware of:

| Feature | MinIO | GCS | S3 |
|---|---|---|---|
| Object versioning | Yes | Yes | Yes |
| Object-level KMS encryption | Via KES | CMEK | SSE-KMS |
| Object Lock / retention | Limited | Retention policies | Object Lock (compliance mode) |
| Presigned URLs | Yes | Yes | Yes |
| Lifecycle rules | ILM | Lifecycle rules | Lifecycle rules |
| Uniform bucket-level access | No ACL support anyway | Available | Bucket policies |
| Cross-region replication | Optional | Turbo replication / dual-region | Cross-region replication |

Backend selection may prefer one over another for a specific institution based on required features (e.g., S3's compliance-mode Object Lock is stronger than MinIO's retention primitives for highly regulated data).

---

## 6. Lifecycle Rules

Lifecycle rules are configured per bucket at provisioning. They are declarative: the provisioning module sets them and they operate autonomously thereafter.

### 6.1 Rules by Tier

**`raw`:**
- Objects transition to lower-cost storage class after 90 days (Nearline on GCS, Standard-IA on S3, if backend supports).
- Objects transition to archival storage after 1 year (Coldline on GCS, Glacier on S3).
- Objects are retained for 7 years minimum.
- Versioning: keep all versions for 7 years, then delete non-current versions.

**`canonical`:**
- Objects remain in standard storage for 30 days (recent snapshots accessed for point-in-time queries).
- Transition to lower-cost storage class after 30 days.
- Objects are retained for 7 years minimum.
- Versioning: keep all versions indefinitely within retention (canonical snapshots are the audit backbone).

**`outputs`:**
- Objects remain in standard storage for 90 days.
- Transition to lower-cost storage class after 90 days.
- Objects are retained for 7 years minimum.
- Versioning: keep all versions indefinitely within retention.

**`temp`:**
- Aggressive deletion: objects are permanently deleted 30 days after last modification.
- No archival transition.
- Versioning disabled (temp is scratch space).

### 6.2 Legal Hold and Regulatory Preservation

Retention rules are baseline minimums. When a regulatory examination is in progress or litigation is anticipated, buckets can be placed under legal hold, which suspends lifecycle deletion until the hold is lifted. This is a manual action requiring approval from AequorOS General Counsel (once retained) or CEO.

Legal hold status is captured in bucket metadata and surfaces in the operational dashboard so that operators are aware.

### 6.3 Deletion Semantics

For `raw`, `canonical`, and `outputs`:
- Delete operations from the `StorageClient` are logical (delete marker in versioning). Objects remain retrievable until lifecycle retention expires.
- Physical deletion before retention expiry requires an out-of-band process with explicit approval and audit trail.

For `temp`:
- Delete operations are physical.

### 6.4 Institution Offboarding

When an institution stops being an AequorOS customer, storage is not deleted immediately. The offboarding process:

1. Institution is marked as offboarded in the AequorOS database.
2. Buckets are locked to read-only (no new writes).
3. Data is retained for the remaining regulatory retention period (typically the balance of 7 years from the last transaction).
4. After retention expiry, the institution can request formal deletion, or AequorOS proceeds with deletion on its own schedule.

During the retention period, the offboarded institution can request an export of their own data. Export is provided in the canonical format plus source-format archives from the `raw` tier.

---

## 7. Encryption and Key Management

### 7.1 Encryption Requirements

- **At rest:** all objects encrypted with AES-256. Customer-managed keys (CMEK / CMK / KES).
- **In transit:** TLS 1.3 for all client-storage communication.
- **Sensitive fields:** for objects containing PII (customer names, account numbers), field-level encryption is applied at the application layer *in addition to* object-level encryption. Application-layer keys are managed separately from storage keys.

### 7.2 Key Management by Backend

**MinIO:**
- MinIO KES (Key Encryption Service) with a HashiCorp Vault backend, if the MinIO deployment includes KES.
- Otherwise, SSE-C (Server-Side Encryption with Customer-provided keys), with per-institution keys stored in the AequorOS Vault instance.
- One key per institution.

**GCS:**
- Cloud KMS with CryptoKeys in the `aequoros-security` project.
- One CryptoKey per institution: `projects/aequoros-security/locations/{region}/keyRings/institutions/cryptoKeys/{institution_id}`.
- CryptoKey rotation: every 90 days.
- CMEK applied at the bucket default level (so all objects inherit the institution's key).

**S3:**
- AWS KMS with Customer Master Keys in the `aequoros-security` AWS account.
- One CMK per institution.
- CMK rotation: every 90 days.
- Bucket-default SSE-KMS applied.

### 7.3 Key Rotation

Automated rotation on all backends:

- KMS handles rotation (new key version created; old versions retained for decryption of existing objects).
- New writes use the new key version.
- Existing objects remain readable via the old version.
- After a key version's grace period (typically 1 year), objects encrypted with old versions are re-encrypted in a background rotation job.

### 7.4 Key Revocation and Institution Data Isolation

If an institution's key is revoked or destroyed, their data becomes unreadable. This is a capability, not a defect: it allows an institution to request cryptographic deletion of their data even before the standard retention period expires (with appropriate legal and regulatory approvals).

Key revocation is a manual action requiring dual approval (AequorOS CTO + General Counsel or equivalent). The revocation is logged and irreversible.

### 7.5 Break-Glass Access

For emergency scenarios (regulator subpoena, forensic investigation), a break-glass mechanism allows access to encrypted data even when the primary access path is unavailable. This mechanism:

- Requires dual approval (AequorOS CTO + General Counsel).
- Uses a separately-managed break-glass key stored offline.
- Is fully audited: the break-glass access itself produces an immutable audit record.
- Is exercised rarely and reviewed annually as part of security posture assessment.

---

## 8. Access Control and Identity

### 8.1 Principle

Access control is enforced at the IAM layer of the backing store. Application code cannot bypass IAM to read another institution's data because the credentials themselves do not have that access. This is stronger than application-layer isolation and is what banks care about in a vendor risk assessment.

### 8.2 Service Account Model

Each institution has a dedicated service account in the cloud provider:

**GCS:** `institution-{institution_id}@aequoros-prod.iam.gserviceaccount.com`
**S3:** `arn:aws:iam::{aequoros-account}:role/institution-{institution_id}`
**MinIO:** MinIO IAM user `institution-{institution_id}`

The service account has permissions strictly limited to that institution's four buckets. It cannot read, write, list, or describe any other institution's buckets or the platform-wide reference bucket for writes.

### 8.3 Application-Layer Identity Federation

Application code does not carry long-lived credentials for institution service accounts. Instead:

- The AequorOS application runs under a platform service account with minimal permissions.
- When a request arrives that is scoped to institution X, the application impersonates (GCS) or assumes (S3) the institution-X service account for the duration of that request.
- Impersonation/assume-role is logged.
- Session credentials are short-lived (typically 1 hour maximum).

This means:
- Application code always operates under the correct institution identity.
- A bug in tenant resolution (application picks the wrong institution) results in an IAM error, not a data leak.
- Audit logs show every institution-scoped operation with the correct identity attached.

### 8.4 Human Access

Human operators (AequorOS engineers, support staff) do not have direct access to institution data in normal operation. Access is provisioned on-demand through a break-glass workflow with:

- Justification recorded
- Time-boxed access grant
- Full audit of what was accessed
- Automatic revocation after the time box expires

Institution users (bank staff using AequorOS) authenticate through the AequorOS application, which handles IAM impersonation on their behalf. They never directly touch the storage backend.

### 8.5 External Auditors

When a bank's external auditor needs read access to their institution's data (regulatory examination, financial audit), a dedicated auditor identity is provisioned:

- Scope: read-only, on the specific institution's `outputs` and `canonical` tiers only
- Duration: time-boxed to the audit period
- Auditor accesses through a dedicated interface (not the primary application), with full audit logging

Auditor access is provisioned by AequorOS at the request of the institution, with approval from the institution's CRO or CFO.

---

## 9. Audit Integration and Lineage

### 9.1 Every Operation Is Logged

Every read, write, list, delete, metadata query, and presigned URL generation produces an audit log entry. Entries include:

- Timestamp (with timezone)
- Operation type
- Storage location (institution, tier, path)
- Version ID (if applicable)
- Requesting identity (service account or user, plus underlying human identity if impersonation is involved)
- Client IP address (if external client)
- Bytes transferred
- Result (success or error class)
- Correlation ID for request tracing
- Lineage node ID linking to the Data Engine's lineage graph (per `data_engine.md` section 8.2)

Logs land in the platform-wide audit bucket (`aequoros-{env}-audit-logs`) in a structured JSON format. Logs are immutable and hash-chained per section 9.3.

### 9.2 Retention

Audit logs are retained for a minimum of 7 years to satisfy banking examination requirements. Some jurisdictions require longer; retention is configurable per environment but never shorter than 7 years for production.

### 9.3 Tamper Evidence

Audit logs are hash-chained: each log entry includes a cryptographic hash of the previous entry. Any modification to a past entry breaks the chain and is detectable by re-verification. Verification runs daily via a scheduled job that traverses the chain and alerts on mismatch.

This is stronger than "audit logs stored in a bucket with write-protection." A malicious actor with sufficient access to tamper with a specific entry would need to rewrite the entire subsequent chain, which is detectable.

### 9.4 Lineage Integration

The Data Engine's lineage graph (per `data_engine.md` section 8.2) references storage objects by their `StorageLocation`. When an examiner queries "what source data produced this CAR calculation," the lineage graph traces back through canonical snapshots and enrichments to the raw ingested files in the `raw` tier. The storage layer's `read()` operation, called with the appropriate lineage traversal, produces the exact objects that fed the calculation.

Lineage across restatements works because canonical objects are versioned and the lineage graph captures the specific version_id of every read.

### 9.5 Reproducibility Guarantee

Given a lineage node ID, the storage layer must be able to reproduce the exact object contents at the point in time referenced. This means:

- Object versions must not be silently deleted within retention.
- KMS key versions must remain available (not destroyed) for the duration of the referenced objects' retention.
- Backend storage class transitions must not change object contents.

This reproducibility is what makes AequorOS auditable to BoG examiners. A regulator asking "reproduce your LCR calculation for December 2027 exactly as reported" gets the exact answer, not a re-calculated approximation.

---

## 10. Multi-Region and Data Residency

### 10.1 Per-Institution Region Assignment

At institution provisioning, a region is selected based on:

1. **Regulatory requirement:** if the institution's regulator mandates in-country data residency, that constrains region choice. Ghana currently does not mandate in-country residency for banking data, but the interpretation is worth confirming with BoG per institution.
2. **Latency:** operational latency between the institution and the storage region. For a Ghanaian bank, `af-south-1` (Cape Town) is closer than European regions.
3. **Cloud provider region availability:** not every cloud provider has presence in every country. Ghana specifically has limited cloud presence as of 2026; `af-south-1` (AWS) and `europe-west1` (GCS) are the practical choices.

Region assignment is captured in the institution record and drives bucket provisioning. Region is not changed after provisioning; a change in residency requirements triggers a migration project, not a bucket-move operation.

### 10.2 Cross-Region Replication

For production environments, cross-region replication is enabled for `raw`, `canonical`, and `outputs` tiers. The replica region is a paired region within the same jurisdiction (e.g., `af-south-1` primary replicating to a compatible secondary if one is later available in Africa; or `europe-west1` primary replicating to `europe-west4`).

Replication provides:
- Disaster recovery: RPO ≤ 1 hour, RTO ≤ 4 hours per `data_engine.md` section 14.6.
- Read availability during regional outages.

Replication does not extend across jurisdictions in ways that would violate data residency (e.g., no replication of Ghana bank data to a US region).

### 10.3 Data Sovereignty

Some institutions may require that data never leaves a specific jurisdiction, even for cloud provider internal replication. In those cases:

- Cross-region replication is disabled.
- Only single-region backup is used.
- The institution accepts a weaker DR posture in exchange for stricter residency.

This is negotiated per institution at onboarding.

---

## 11. Migration Path: MinIO to Managed Cloud

### 11.1 When Migration Happens

MinIO is authorized for MVP with synthetic data only. Migration to a managed cloud backend is required before the first paying bank customer's data lands. The trigger events, whichever comes first:

- First LOI signed with a paying customer
- 6 months after MVP MinIO deployment
- Any indication a real bank customer's data will need to be stored

### 11.2 Migration Approach

The migration exploits the abstraction defined in section 4. Because application code depends on `StorageClient`, not on any specific backend, migration is fundamentally a data-move operation, not a code rewrite.

**Steps:**

1. **Provision production cloud storage.** Terraform module (per section 3.3) creates all target buckets in GCS or S3, with KMS keys, IAM, lifecycle, and audit sinks configured.
2. **Copy MVP data to production.** For each institution's MVP data (all synthetic during MVP phase), copy objects from MinIO to the corresponding cloud bucket. Preserve custom metadata, checksums, and version history. Use `rclone` or a purpose-built migration script; validate checksums after copy.
3. **Update storage configuration.** Change `backend: 'minio'` to `backend: 'gcs'` (or `s3`) in the config. Restart the service.
4. **Validate.** Run the storage contract test suite against the new backend to confirm behavior. Run Data Engine end-to-end tests to confirm the calculation modules still produce identical outputs from the migrated data.
5. **Decommission MinIO.** After a burn-in period (typically 2 weeks), the MinIO instance is retired.

### 11.3 Migration for Live Data (When Real Banks Are On the Platform)

If migration happens *after* a real bank is live (which should not happen if we follow section 11.1 correctly, but is documented here for defensive planning):

- Migration must be zero-downtime for the customer.
- Dual-write during migration: writes go to both old and new backends until cutover.
- Read-through-old-fallback-to-new during transition.
- Full checksum validation before cutover.
- Regulator notification (if the migration crosses jurisdictional boundaries).

This is meaningfully more complex than the MVP-time migration and should be avoided by migrating early.

### 11.4 Rollback

If migration fails, rollback is straightforward for MVP data: revert the configuration to MinIO. The MinIO instance retains its state during migration; nothing is deleted from source until the burn-in period completes.

---

## 12. Phasing and Milestones

Phasing aligns with `data_engine.md` section 15.

### 12.1 Phase 1: MVP (Months 1-9 of `data_engine.md`)

- MinIO deployment configured with bucket-per-institution-per-tier for the Sample Bank Limited synthetic dataset.
- `StorageClient` interface implemented with the MinIO backend.
- Contract tests passing against MinIO.
- Provisioning script for MinIO buckets.
- Access logging to a MinIO audit bucket (hash-chained).
- Lifecycle rules on `temp` tier.
- KES or SSE-C encryption per institution.
- Data Engine layers 1-5 fully integrated with storage.

Deferred to later phases:
- GCS and S3 backends (skeleton only)
- Cross-region replication
- Legal hold workflow
- Break-glass access mechanism (defer to Phase 3)

### 12.2 Phase 2: Production Cloud Migration (Months 9-15 of `data_engine.md`)

Aligned with first paying bank customer.

- GCS backend fully implemented, contract tests passing.
- Terraform provisioning module for GCS at institution granularity.
- KMS integration with CMEK keys per institution.
- Cloud Audit Logs sink to platform audit bucket.
- Migration of MVP data from MinIO to GCS.
- Decommission MVP MinIO once first bank goes live.
- Legal hold workflow implemented.

### 12.3 Phase 3: Feature Maturity (Months 15-24)

- S3 backend fully implemented for banks requiring AWS.
- Cross-region replication configured for prod environments.
- Break-glass access mechanism implemented and tested.
- Key rotation automation in production.
- Storage cost monitoring and optimization.

### 12.4 Phase 4: Advanced Compliance (Year 3+)

- SOC 2 Type II certification of the storage layer (as part of overall AequorOS SOC 2).
- ISO 27001 alignment.
- Per-institution encryption key sovereignty (BYOK for banks that want to hold their own keys).
- Additional cloud provider backends (Azure Blob Storage) if driven by customer demand.

### 12.5 Phase 5: Non-Bank Institution Categories (Year 3+ per `data_engine.md` section 15.5)

- Storage layer generalizes cleanly to non-bank institutions because the bucket-per-institution-per-tier pattern is agnostic to institution type.
- Region assignment logic extends to jurisdictions where pension funds and insurance companies operate.
- Retention rules extend to institution-category-specific requirements (some jurisdictions require longer retention for pension records than for banking records).

---

## 13. Non-Goals and Explicit Deferrals

Things this document deliberately does not specify:

- **The block storage layer** (databases, volumes for compute instances). Storage-as-in-object-store only.
- **The data warehouse** (Snowflake, BigQuery). That is a Data Engine analytical concern, not object storage.
- **Application-layer caching** (Redis, memcached). Caching is a service-level concern.
- **Backup for the audit bucket itself.** The audit bucket is its own backup by virtue of the hash chain; additional physical backup is an infrastructure concern.
- **Cost optimization strategies at scale.** Deferred to Phase 3.

Things this document is explicit about *not* wanting:

- **Shared-bucket multi-tenancy for production data.** Prohibited.
- **Long-lived credentials in application code.** All access is via short-lived impersonated credentials.
- **Direct human access to storage in production.** Only via break-glass.
- **Any physical deletion of retained-tier data without dual approval.**

---

## 14. Implementation Guidance for AI-Assisted Coding

If this document is handed to Claude Fable 5 or another AI coding assistant to implement, the following instructions apply and extend those in `data_engine.md` section 17:

1. **Build the `StorageClient` abstraction first.** Before any concrete backend, the interface (section 4.2) and error hierarchy (section 4.3) must exist. Then build MinIO, then GCS, then S3.

2. **Contract tests are non-negotiable.** Write them once. Run them against every backend implementation. If a test fails for one backend and passes for another, the abstraction is leaking and must be fixed.

3. **Do not implement break-glass or advanced encryption features in MVP.** Section 12 phases these. MVP is MinIO with basic KES/SSE-C. Everything else is later.

4. **Do not couple to a specific backend from application code.** Application code, including Data Engine adapters, calculation engines, and audit substrate, depends on `StorageClient` only. Any temptation to reach around it (e.g., "just use boto3 directly for this one thing") must be refused. Add the capability to the interface if truly needed.

5. **When implementing bucket provisioning, use the tools each backend expects:** Terraform for GCS and S3, Python admin API for MinIO. Do not attempt to unify these into a single tool; each has different failure modes and semantics.

6. **Object metadata is mandatory on every write.** Missing metadata is a write failure (`StorageValidationError`). No shortcuts.

7. **Every access log entry connects to Data Engine lineage.** If an operation cannot supply a lineage_node_id, either the operation is out of scope or the caller has failed to properly integrate with the Data Engine. Do not silently accept null lineage; treat it as a validation error in production and a warning in dev.

8. **Do not use S3 or GCS "convenience" features that break the abstraction.** For example, S3 Select or GCS Storage Insights are backend-specific and cannot be exposed through `StorageClient`. If a use case genuinely requires them, add a well-defined method to the interface first.

9. **Preserve the MinIO retirement date.** Section 5.2 specifies an explicit retirement date on MVP MinIO environments. Do not remove or extend this check without explicit approval from Dela or Eric. It exists to prevent MVP infrastructure from silently becoming production infrastructure.

10. **When in doubt, defer to `data_engine.md`.** If a storage design choice conflicts with a Data Engine design choice, the Data Engine wins. Surface the conflict for resolution.

---

## Appendix A: Terminology

- **Backing store.** The underlying object storage system (MinIO, GCS, S3) that implements the `StorageClient` interface.
- **Bucket.** A container for objects in the backing store. AequorOS uses one bucket per (institution, tier).
- **CMEK / CMK.** Customer-Managed Encryption Keys (GCP) / Customer Master Keys (AWS). Encryption keys held by AequorOS in a KMS, applied to objects at write time.
- **Institution.** A tenant of AequorOS. In Phase 1 typically a bank; later pension funds, insurers, etc.
- **KES.** MinIO's Key Encryption Service.
- **Object.** A file stored in a bucket, with a path and custom metadata.
- **Presigned URL.** A time-limited URL granting direct client access to a specific object without requiring the client to hold AequorOS credentials.
- **Tier.** One of `raw`, `canonical`, `outputs`, `temp`. Data classification determining lifecycle and access rules.
- **Version.** A specific point-in-time state of an object. Versioning-enabled buckets retain all prior versions.

## Appendix B: Related Documents

- `data_engine.md` — the Data Engine specification this storage layer supports
- `/schema/canonical_v1/` — DDL for the canonical model (referenced from `raw`/`canonical` tier semantics)
- `/infrastructure/README.md` — cloud infrastructure provisioning (Terraform modules referenced here)
- `/security/access_control.md` — application-layer identity and RBAC (complements storage-layer IAM)
- `/product/onboarding_playbook.md` — the customer onboarding process that triggers institution provisioning
- `sample_bank_data/README.md` — the synthetic reference dataset used against MVP MinIO

---

**End of Storage Layer Specification v1.0**

*Revisions expected as production migration proceeds, as GCS vs. S3 tradeoffs are validated against first bank customers, and as data residency expectations from BoG and other African regulators mature.*
