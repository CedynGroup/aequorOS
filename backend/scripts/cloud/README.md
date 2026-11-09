# Cloud container setup

Scripts that prepare a fresh Linux cloud container (root, Ubuntu 24.04) to run the whole test suite.
The container's setup script only needs to call `backend/scripts/cloud/setup.sh`; each service owns the
script for what it needs, so they are reviewed and versioned with the code they wrap. Nothing here is used
by CI or by deployments.

```sh
bash backend/scripts/cloud/setup.sh   # idempotent; a second run takes seconds
```

`setup.sh` only calls the scripts below, in this order. Each is also runnable alone once the first two
have run (the service scripts stop with a message if the toolchain or the `aqs` user is missing).

| Script                                   | Owns                                                                                                                                                                                                        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/scripts/cloud/toolchain.sh`     | Shared: the checksum-pinned mise, the `mise.toml` toolchain, `mise run setup` (one `pnpm install` over every workspace project, plus the backend `uv sync`), `no-mistakes`, `/etc/profile.d/zz-aequoros.sh` |
| `backend/scripts/cloud/setup-backend.sh` | `lsof`, PostgreSQL 17, the pinned MinIO and OpenBao releases (checksum-verified), the `aqs` test user (`initdb` refuses root), the CA bundle, `aequoros-services`, `aequoros-openbao`                       |
| `backend/dashboard/cloud/setup.sh`       | Docker (`docker.io` when missing), Playwright Chromium matching the dashboard's `@playwright/test` pin (`/opt/pw-dashboard`), `aequoros-kms`, `aequoros-dashboard-e2e`                                      |

`setup-lib.sh` (the `fetch` checksum helper, `require_base`, `link_wrapper`) is shared by the three;
`lib.sh` is shared by the wrappers. Re-running `setup.sh` re-links every wrapper into `/usr/local/bin`.

## Services that need no setup script

After `setup.sh` (really, after the one `pnpm install` in `mise run setup`) these were run as root on the
pinned toolchain with no further system packages, services, browser or network secrets:

| Surface                    | Command                                                               | Result (this container)          |
| -------------------------- | --------------------------------------------------------------------- | -------------------------------- |
| Marketing site             | `pnpm --filter @aequoros/frontend lint` / `build`                     | pass, build about 26 s           |
| Operator console           | `pnpm --filter @aequoros/console typecheck` / `test` / `build`        | pass, build about 21 s           |
| Generated API client       | `pnpm --filter @aequoros/risk-service-api exec tsc --noEmit` / `test` | pass                             |
| Dashboard unit job         | `pnpm --filter dashboard test:coverage` (CI's `Test dashboard`)       | 73 files, 939 tests, about 4 min |
| Dashboard production build | `pnpm --filter dashboard build`                                       | pass, about 1 min                |

The dashboard unit job loads backend contracts through `uv run`, so it needs the backend `.venv` that
`mise run setup` already syncs. Builds write `next-env.d.ts` and `.next`; a root-run build leaves
`next-env.d.ts` root-owned, and `aequoros-dashboard-e2e` hands it back to `aqs`.

## Wrappers

| Command                                           | Runs                                                                          |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `aequoros-services --postgres --storage -- <cmd>` | `local_services.py run` as `aqs`; use `.venv/bin/python -m pytest ...` as cmd |
| `aequoros-dashboard-e2e [playwright args]`        | the dashboard Playwright suite, for example `--shard=1/4 --trace off`         |
| `aequoros-kms start\|stop`                        | the pinned local-kms emulator on `127.0.0.1:18080` (starts Docker if needed)  |
| `aequoros-openbao start\|stop`                    | an OpenBao dev server; export the printed `OPENBAO_TEST_*` variables          |

`aequoros-kms` (owned by the dashboard, also used by backend tests) needs a Docker daemon: the dashboard script installs `docker.io` when Docker is missing, but `dockerd`
only starts in a VM or a privileged container. Elsewhere the command stops with that reason, and the
Postgres, MinIO, OpenBao and dashboard commands keep working without it. A Docker socket mounted from the host also works, but the container then needs the host network namespace, because the emulator's port is published on the host's loopback.

`toolchain.sh` also installs the `no-mistakes` CLI at the version `.no-mistakes.yaml` pins (SHA256-verified) into
`/opt/no-mistakes` and links it onto the `PATH`. It never runs it, so `no-mistakes init` stays a per-repository
choice. The pipeline's AI stages need an agent CLI and credentials that setup does not provide.

Pass `TEST_KMS_ENDPOINT_URL` (backend) or `BANK_KMS_ENDPOINT_URL` (dashboard) as
`http://127.0.0.1:18080` after `aequoros-kms start`. Set `AEQUOROS_ROOT` to another git worktree to
run two shards at once; a worktree has its own services, ports and run lock, but needs its own
`mise run setup`. Four CPUs support about two concurrent dashboard shards.

Bumping a pinned release (including `no-mistakes`, whose release publishes no checksum) means updating both the script that holds the pin (`toolchain.sh` or `setup-backend.sh`) and `.github/actions/setup-minio`,
`.github/actions/setup-openbao` or the workflow.
