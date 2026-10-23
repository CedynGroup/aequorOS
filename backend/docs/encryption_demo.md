# aequorOS live key revocation demo

This walkthrough uses a disposable SQLite book, synthetic identities and the
existing local test provider. It never contacts a bank KMS service. The control
has no HTTP route or production UI. Both control and provider refuse every
`APP_ENV` except `local` and `test`; configuring `DEMO_BANK_KEY_FILE` in a deployed
environment also refuses application startup.

## Prepare once

Use a throwaway directory outside the checkout. Start from the repository's
existing hermetic E2E stack and its synthetic book, following
[the dashboard E2E guide](../dashboard/README.md#end-to-end-playwright). Do not
connect these controls to a live database. The CLI accepts only `demo.db` in its
explicit directory, with no database URL argument or environment fallback.

For a separately launched demo stack, bootstrap the existing E2E fixture into
`$DEMO_DIR/demo.db`, using `APP_ENV=test`, the E2E signing/authentication settings
from `backend/dashboard/playwright.config.ts`, and a working directory outside
the checkout so developer `.env` files are not loaded. Use the existing
`backend/scripts/e2e_bootstrap.py`; all fixture data is synthetic. Keep its
software signing-key directory in the same throwaway directory. No worker or
external key service is needed.

From `backend/`, initialize and connect the synthetic key:

```sh
.venv/bin/python scripts/demo_bank_key.py init --directory "$DEMO_DIR" \
  --organization OR-DEM00001 --bank BK-SAMP0001
.venv/bin/python scripts/demo_bank_key.py connect --directory "$DEMO_DIR"
```

Run the demo API with `APP_ENV=test`,
`DATABASE_URL=sqlite+pysqlite:///$DEMO_DIR/demo.db`,
`DEMO_BANK_KEY_FILE=$DEMO_DIR/demo-key.json`, and `RUN_INPROCESS_WORKER=0`.
Use loopback ports and point the dashboard's `NEXT_PUBLIC_RISK_API_BASE_URL` to
that API. Run from the throwaway directory with the backend on `PYTHONPATH`, so
the demo cannot pick up a developer's `.env` file.

Sign in using the E2E fixture's local test account. In Institution → Related
parties, add **Synthetic Demo Director** after connecting the key. Open the page
again to verify the name. Existing fixture rows predate key connection and are
not re-encrypted. Use this newly created protected record for the walkthrough.
Keep a numeric dashboard or certified analytics pack open in a second tab.

## Walkthrough — under one minute

| Time | Screen / action | What changes | What to say |
| --- | --- | --- | --- |
| 0–10 s | Related parties; show Synthetic Demo Director | The protected name is readable | “The bank owns access to this identity.” |
| 10–20 s | Run `revoke` below; refresh Related parties | The name becomes a lock placeholder; the bank-revocation message appears | “The bank has turned off aequorOS access. We cannot reveal the protected field.” |
| 20–30 s | Numeric dashboard / certified pack; refresh | Ratios and numeric analytics still load | “The numeric view remains available.” |
| 30–40 s | Return to Related parties; run `restore` | Within the five-second recovery poll, the original name returns | “The bank restored access. Nothing was re-imported or re-encrypted.” |
| 40–55 s | Optionally run `outage`, refresh, then `restore` | The distinct key-service outage message appears, then recovers | “An unreachable service is different from the bank revoking access.” |

```sh
.venv/bin/python scripts/demo_bank_key.py revoke  --directory "$DEMO_DIR"
.venv/bin/python scripts/demo_bank_key.py restore --directory "$DEMO_DIR"
# Optional outage variant:
.venv/bin/python scripts/demo_bank_key.py outage  --directory "$DEMO_DIR"
.venv/bin/python scripts/demo_bank_key.py restore --directory "$DEMO_DIR"
```

The CLI rereads and atomically updates only the private state file. Each provider
operation rereads that file; API restarts are unnecessary. Reinitialization
refuses to replace an existing key. Connecting refuses to replace another key,
including a real KMS key. Restore changes status only, retaining exactly the same
key material and ciphertext. Keep the key file for the lifetime of its disposable
book; never regenerate it over retained ciphertext.

To demonstrate draft preservation, open a related-party edit before revocation,
change its name, revoke and press Save. The clear revocation message appears and
the typed draft stays in the form. Restore and press Save again. Staff inspection
shows placeholders even when the synthetic key is active.

Encrypted saved dashboard definitions and filing snapshots are themselves
protected payloads: show their locked panels separately. Use numeric dashboards,
certified packs, filing lists and status metadata to demonstrate availability.

## Verify and collect proof

```sh
.venv/bin/pytest tests/storage/test_demo_key.py \
  tests/core/test_bank_field_encryption.py tests/storage/test_key_providers.py -q
```

The tests exercise repeated revoke/outage/restore loops across fresh provider
instances, key-material preservation, production refusal, bank scoping, locked
HTTP reads, write refusal, recovery and numeric BI while keys are disabled or
unreachable. The dashboard's `unit/protected-data.spec.tsx` checks the visible
messages, Retry, recovery polling and removal of previously decrypted cache data.
Capture readable, revoked, outage and recovered screenshots in the throwaway
proof directory; attach them to the review, never commit them.
