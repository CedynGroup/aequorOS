# Cloud container setup

Scripts that prepare a fresh Linux cloud container (root, Ubuntu 24.04) to run the whole test suite.
The container's setup script only needs to call `setup.sh`; the logic lives here so it is reviewed
and versioned with the code it wraps. Nothing here is used by CI or by deployments.

```sh
bash backend/scripts/cloud/setup.sh   # idempotent
```

`setup.sh` installs the pinned toolchain from `mise.toml`, PostgreSQL 17, the MinIO and OpenBao
releases CI pins (checksum-verified), a Playwright Chromium matching the dashboard's pin, and links
the wrappers below into `/usr/local/bin`. The test user `aqs` exists because `initdb` refuses root.

| Command                                           | Runs                                                                          |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `aequoros-services --postgres --storage -- <cmd>` | `local_services.py run` as `aqs`; use `.venv/bin/python -m pytest ...` as cmd |
| `aequoros-dashboard-e2e [playwright args]`        | the dashboard Playwright suite, for example `--shard=1/4 --trace off`         |
| `aequoros-kms start\|stop`                        | the pinned local-kms emulator on `127.0.0.1:18080` (starts Docker if needed)  |
| `aequoros-openbao start\|stop`                    | an OpenBao dev server; export the printed `OPENBAO_TEST_*` variables          |

Pass `TEST_KMS_ENDPOINT_URL` (backend) or `BANK_KMS_ENDPOINT_URL` (dashboard) as
`http://127.0.0.1:18080` after `aequoros-kms start`. Set `AEQUOROS_ROOT` to another git worktree to
run two shards at once; a worktree has its own services, ports and run lock, but needs its own
`mise run setup`. Four CPUs support about two concurrent dashboard shards.

Bumping a pinned release means updating both here and in `.github/actions/setup-minio`,
`.github/actions/setup-openbao` or the workflow.
