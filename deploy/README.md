# Deployment

Deployed stacks run as Coolify Docker Compose applications.
[`openbao/`](openbao/docker-compose.openbao.yml) is the signing-key custody
stack and the worked example of the rules below.

## Coolify compose rules

- **Coolify compose apps: never use dollar-brace variable interpolation in deploy compose
  files** (2026-07-21 incident: Coolify parses compose text — comments included — and
  auto-seeds a UI env row per reference; with required-with-message guards it stored the
  message text as VALUES and duplicated rows every deploy, corrupting the backend app's
  env store until the resource was recreated). Pattern: services load `env_file: .env`
  (Coolify writes it from its UI); fail-fast lives in the app's settings validators.
  Exception: build args (dashboard NEXT_PUBLIC_*) must stay interpolated — keep guards
  bare `:?` with no message text.
- **Coolify compose apps get ONLY the compose file on the host — never bind-mount a
  repo file** (2026-07-26, two failed OpenBao deploys). The Docker Compose build pack
  materialises the normalised compose plus its own `.env`/`README.md`; the repository is
  not checked out (that is the off-by-default "Preserve Repository During Deployment"
  toggle). A `- ./x.conf:/etc/x.conf` bind therefore has no source, Docker CREATES it as
  an empty directory, and the created directory then blocks any corrected checkout — so
  fixing the path alone cannot recover it (needs an `rmdir` on the host, with the
  container stopped, or it is recreated on the next restart). `create_host_path: false`
  does not save you: Coolify rewrites long-syntax mounts to short form and drops it.
  Put config INSIDE the compose (a `command:` heredoc) — `deploy/openbao/` is the
  worked example.
