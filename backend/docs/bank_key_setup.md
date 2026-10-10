# Bank key setup

The current implementation supplies provider-independent key operations in
`app/core/key_management`, an AWS KMS adapter, and AWS Encryption SDK file
transforms. Onboarding, storage writes and reads, and application rotation do
not yet consume these primitives. Supplying a key reference alone does not
enable bank-held encryption of S3 files.

Supply the exact ARN of the bank's customer-managed symmetric AWS KMS encryption
key, together with its AWS account ID and region. The ARN must identify a key
using `arn:<partition>:kms:<region>:<bank-account-id>:key/<key-id>`, and its account
and region must match the declared values. The bank account must differ from the
platform account, configured through `ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` in
deployed environments. The key must be enabled for encryption operations.
The adapter uses the workload's AWS role credentials; the bank must authorize
that role to use its key. Transport requirements are governed by
[the transport contract](transport_security.md).

Alias ARNs (`arn:…:alias/…`), alias names and bare key IDs are rejected during
key validation. The error is: “An exact AWS KMS key ARN is required; alias ARNs
are not accepted.” Supply the key ARN when connecting the bank key during
onboarding once that integration is available. An alias can be repointed to a
different key, so it cannot provide the fixed key identity needed for decryption
and audit evidence.

To move to a different master key, the provider's explicit `rotate` operation
takes the exact source and destination key ARNs and rewraps a data key without
changing its encrypted payload. The consuming application must persist the
replacement envelope and key reference before retiring access to the source
key; repointing an AWS alias does not rotate existing encrypted data.
