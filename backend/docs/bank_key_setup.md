# Bank key setup

Supply the exact ARN of the bank's customer-managed symmetric AWS KMS encryption
key, together with its AWS account ID and region. The ARN must identify a key
using `arn:<partition>:kms:<region>:<bank-account-id>:key/<key-id>`, and its account
and region must match the declared values. The bank account must differ from the
platform account, configured through `ENCRYPTION_PLATFORM_AWS_ACCOUNT_ID` in
deployed environments. The key must be enabled for encryption operations.

Alias ARNs (`arn:…:alias/…`), alias names and bare key IDs are rejected during
key validation. The error is: “An exact AWS KMS key ARN is required; alias ARNs
are not accepted.” Supply the key ARN when connecting the bank key during
onboarding. An alias can be repointed to a different key, so it cannot provide
the fixed key identity needed for decryption and audit evidence.

To move to a different master key, use the explicit rotation flow with the exact
source and destination key ARNs. Retain access to the source key until rotation
has completed; repointing an AWS alias does not rotate existing encrypted data.
