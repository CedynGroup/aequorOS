# Security Policy

AequorOS is treasury and regulatory software for financial institutions; we take
security reports seriously and appreciate responsible disclosure.

## Reporting a vulnerability

- Email **eric@aequoros.com** with subject `SECURITY: <short summary>`.
- Include reproduction steps, affected component/endpoint, and impact assessment.
- Please do **not** open public GitHub issues for security reports.

You will receive an acknowledgement within **3 business days** and a remediation
plan or status update within **14 days**. Please allow us reasonable time to fix
and roll out before any public disclosure.

## Scope notes

- Demo/sandbox surfaces (the synthetic Sample Bank dataset, the ORASS sandbox
  submission simulator) are explicitly non-production; findings there are still
  welcome but triaged at lower severity.
- Credentials for vendor market-data connections are stored AES-256-GCM
  encrypted and are write-only at the API. Reports involving credential
  handling, tenant isolation (RLS), or lineage integrity are highest priority.

## Supported versions

The `main` branch is the supported line during the MVP phase.
