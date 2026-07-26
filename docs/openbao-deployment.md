# Deploying OpenBao for attestation signing

OpenBao holds the officer signing keys and runs the certificate authority that
issues their certificates. It is the difference between a deployment that can
file a signed regulatory return and one that cannot: `SIGNING_BACKEND=software`
refuses to initialise under `APP_ENV=production` — a key the application can
itself decrypt is not a key under the signatory's sole control.

It runs on its **own host**. If the application VPS is compromised, an attacker
holds a scoped AppRole token, not key material.

Artifacts: `deploy/openbao/docker-compose.openbao.yml`, `deploy/openbao/openbao.hcl`,
`backend/scripts/bootstrap_openbao_pki.py`.

---

## 1. The host

A small VPS that runs nothing else. Then:

- **DNS** — point `bao.aequoros.com` at it.
- **TLS** — let the reverse proxy terminate with a public certificate. This is
  why `openbao.hcl` sets `tls_disable = true` on the listener: the proxy is on
  the same host and the listener is never published. It also means the
  application needs no `OPENBAO_CA_CERT`, which matters because that path has
  never been exercised against a real private CA.
- **Firewall** — allow 443 **only from the application VPS**. OpenBao publicly
  reachable is a materially worse position even behind TLS; there is no reason
  for anything but the API to talk to it.
- **Ports 8200/8201 must not be published to the host network.** Traffic reaches
  the container over the proxy's internal network.
- **Disable or encrypt swap.** Not optional, and easy to skip because nothing
  will complain. OpenBao 2.x removed mlock — the setting that used to keep key
  material out of swap now makes the server refuse to start — so this is the
  host's job:

  ```bash
  swapoff -a && sed -i '/ swap /s/^/#/' /etc/fstab
  ```

  Without it, decrypted key material can be paged to disk and survive a reboot
  in plaintext, which quietly falsifies "the key never leaves the process".

Deploy through Coolify with Base Directory `/deploy/openbao` and Compose
Location `/docker-compose.openbao.yml`.

**No environment variables to set.** The service needs no `.env`: everything is
in `openbao.hcl`, and the one value the CLI wants (`BAO_ADDR`) is a literal in
the compose. A fresh deploy comes up **sealed and reporting healthy** — that is
intended. The healthcheck is liveness, not readiness, because an unhealthy
container gets restarted and a restarting container can never be unsealed.

## 2. Initialise — once, and never again

```bash
docker exec -it openbao bao operator init -key-shares=5 -key-threshold=3
```

This prints **five unseal shares and a root token**. They are printed once.

- Distribute the shares to five people. Three are needed to unseal, so no single
  person can, and two can be unavailable.
- **Do not store any share on this host.** A share on the machine it unseals is
  not a share, it is a key sitting next to the lock.
- Store the root token offline and revoke it after step 4. It is not needed in
  normal operation.

## 3. Unseal

```bash
docker exec -it openbao bao operator unseal   # ×3, different holders
```

**Required after every restart.** A sealed OpenBao answers 503 and cannot sign;
the platform keeps running and returns keep generating, but certification stops.

Rehearse this before a bank depends on it. The failure you are rehearsing for is
an unplanned reboot at 09:00 on a DBK morning, and the time to discover that
nobody remembers who holds share three is not then.

## 4. Bootstrap the PKI and the AppRole

From `backend/`, against the initialised server:

```bash
PYTHONPATH=. uv run python scripts/bootstrap_openbao_pki.py --addr https://bao.aequoros.com --token <root-token> --trust-roots ./attestation-root.pem
```

This mounts the PKI engines, generates the root and issuing CA, creates the
signing role with `digitalSignature` and `nonRepudiation` key usage, configures
the CRL and OCSP URLs, writes the ACL policy the application's AppRole uses, and
saves the trust anchor. It prints the settings to apply.

Then create the AppRole the application authenticates with, and enable an audit
device — every key use should be recorded somewhere the bank can read:

```bash
bao auth enable approle
bao write auth/approle/role/aequoros token_policies=aequoros-signer token_ttl=30m
bao read  auth/approle/role/aequoros/role-id
bao write -f auth/approle/role/aequoros/secret-id
bao audit enable file file_path=/openbao/file/audit.log
```

Enable the audit device **after** initialisation, never in the config file: an
audit device that cannot write makes OpenBao refuse every request, including the
ones needed to fix it.

## 5. Point the application at it

On the **API** deployment (Coolify → risk-api → Environment):

```
SIGNING_BACKEND=openbao
ATTESTATION_SIGNING_ENABLED=1
SIGNER_ID_PEPPER=<generated once, never rotated>
OPENBAO_ADDR=https://bao.aequoros.com
OPENBAO_ROLE_ID=<from step 4>
OPENBAO_SECRET_ID=<from step 4>
OPENBAO_PKI_MOUNT=pki-int
OPENBAO_PKI_ROLE=aequoros-signer
ATTESTATION_TRUST_ROOTS=/path/to/attestation-root.pem
```

`SIGNER_ID_PEPPER` derives permanent `SGN-` identities which are then persisted
as the authority. Set it once and never rotate it: a new pepper will not rebrand
existing signers, but it derives different identities for anyone provisioned
afterwards.

Without `ATTESTATION_TRUST_ROOTS` the platform still signs, but verification
anchors on the chain each signature carries rather than on your own root, and
reports `trust_anchor: "embedded_chain"`. The API logs a warning at startup
naming the setting.

## 6. Confirm

```bash
curl -s https://api.aequoros.com/api/health/ready
```

Signing gaps are reported here in production. Then certify a return end to end
and download it: the signature block should carry the officer's name and their
`SGN-` id, and Verify should report an institutional trust anchor rather than
`embedded_chain`.

---

## Backup

Back up the Raft store. Note the path is `/openbao/file`, not `/openbao/data`:
the image ships that directory owned by its runtime user, and a volume mounted
anywhere else is created root-owned and the server cannot write to it.

```bash
docker exec openbao bao operator raft snapshot save /openbao/file/snapshot.snap
```

Take it off the host. Restoring needs the snapshot **and** the unseal shares —
they are not interchangeable and neither alone is sufficient.

**What losing OpenBao actually costs.** Returns already filed stay verifiable
forever: a PAdES signature carries the certificate chain inside the PDF, and
verification re-hashes the document rather than consulting the key store. The
offline CLI (`scripts/verify_attestation.py`) proves a filed return with no
infrastructure at all. What you lose is the ability to sign *new* returns and to
rotate. Bad, recoverable — not years of filings becoming unverifiable.

## Before a bank depends on this

- **Three Raft nodes, not one.** A single node is a single point of failure for
  signing. Because storage is already Raft, adding nodes is a join rather than a
  migration — but do it before the dependency exists, not after.
- **Unseal rehearsed**, with the share holders identified and reachable.
- **Audit log shipped** somewhere outside this host.
- **A trusted timestamp authority.** Without RFC 3161 the signature records the
  server's own clock and reads "server clock — no trusted timestamp"; PAdES
  stays at B-B rather than the B-LTA the design targets. Certificate revocation
  can then retroactively undermine past signatures, which is the whole reason
  long-term validation exists. This is a procurement decision, still open.
