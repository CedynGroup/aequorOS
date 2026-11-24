# Deploying the AequorOS Reporting Gateway (Coolify)

One Coolify **Docker Compose** application runs the gateway:
[`../docker-compose.prod.yml`](../docker-compose.prod.yml).

| Service | What it is | Public host |
| --- | --- | --- |
| `srg-gateway-migrate` | One-shot: migrates the schema as the owner, then exits | — |
| `srg-gateway-api` | API (port 8200): the institution API, the operator API, the supervisor API and the staff sign-in provider at `/idp` | The gateway host, routed by [`traefik-gateway.yml`](traefik-gateway.yml) with mutual TLS |
| `srg-gateway-portal` | AequorOS Portal (port 3100) for supervisors | The portal host, set as a Coolify domain |

The compose file follows the repository's Coolify rules (`deploy/README.md` at the
repository root): no variable interpolation anywhere in it, no bind mount, and no
bundled database.

## Before you start: production key custody

`GATEWAY_ENV=production` refuses to start by design. It needs the gateway's receipt
key, audit key and client-certificate CA held in a KMS or HSM, and that key provider
is not built yet. Until it is, deploy with **`GATEWAY_ENV=sandbox`**. Sandbox runs the
same code, with the keys and CA as files on the `gateway-keys` volume. In sandbox the
portal shows "Sandbox · not official filings", which is accurate while the keys are
files. See [Production](#production) for what lifts it.

## 1. Database

Use the managed Postgres; nothing in the compose file bundles one. Create a database,
an owner that migrates it, and a separate role the API connects as:

```sql
CREATE ROLE srg_owner LOGIN PASSWORD 'choose-a-strong-password';
CREATE ROLE srg_api LOGIN PASSWORD 'choose-another-strong-password';
CREATE DATABASE srg_gateway OWNER srg_owner;
```

The migrations grant `srg_api` only what the API needs. It never gets UPDATE, DELETE
or TRUNCATE on the audit chain, and never DELETE on filed evidence. Triggers refuse
those changes as well.

## 2. The Coolify application

New resource → **Docker Compose** from this repository:

- Base Directory: `/gateway`
- Docker Compose Location: `/docker-compose.prod.yml`
- Domains: set the portal host on `srg-gateway-portal`, container port **3100**. Give
  `srg-gateway-api` **no domain**: its host is routed in step 6.

## 3. Environment variables (Coolify → Environment Variables)

| Variable | Value | Read by |
| --- | --- | --- |
| `GATEWAY_ENV` | `sandbox` | all |
| `GATEWAY_DATABASE_URL` | `postgresql+psycopg://srg_api:…@<db-host>:<port>/srg_gateway?sslmode=require` | API |
| `GATEWAY_MIGRATION_DATABASE_URL` | the same database as `srg_owner` | migrate only (blanked for the others) |
| `GATEWAY_DB_APP_ROLE` | `srg_api` | migrate: the role it grants |
| `GATEWAY_BASE_URL` | `https://<gateway host>`: issuer of access tokens and of the sign-in provider | API |
| `GATEWAY_OIDC_ISSUER` | `https://<gateway host>/idp` | portal (the API reads its own `/idp` as its own provider) |
| `GATEWAY_OIDC_CLIENT_ID` | `srg-portal` | portal: **required** outside local/test |
| `GATEWAY_IDP_REDIRECT_PREFIXES` | `https://<portal host>/` | API: where sign-in may return |
| `GATEWAY_PORTAL_BASE_URL` | `https://<portal host>` | portal |
| `GATEWAY_PORTAL_SESSION_SECRET` | at least 32 random characters (`openssl rand -base64 48`) | portal (blanked for the others) |
| `GATEWAY_TRUSTED_PROXIES` | the proxy's network, from step 6 | API |
| `GATEWAY_OPERATOR_CLIENTS` | from step 5 | API |

`GATEWAY_API_URL` is set in the compose file itself. The portal reaches the API over
the application's private network, never through the public host.

## 4. First deploy, return definitions and supervisor accounts

Deploy. The migrate service runs first. On its first start the API writes the receipt
and audit keys to the `gateway-keys` volume, and `/health/ready` turns healthy.

In Coolify, open a terminal on `srg-gateway-api` and publish the return definitions.
They are definitions only, with no institutions, filings or queries:

```bash
python -m app.seed --definitions-only
```

Create each supervisor's account. The command reads the password from standard
input, so it stays out of shell history:

```bash
python -m app.idp create --email name@example.org --name "Full Name" --group srg-senior-supervisors --password-stdin
```

Groups: `srg-supervisors`, `srg-senior-supervisors`, `srg-returns-policy`,
`srg-access-approvers`, `srg-internal-audit`. Other account commands:
`python -m app.idp list|set-password|disable|enable`.

## 5. Issue the AequorOS operator

Outside local/test no development operator exists. The AequorOS platform enrols banks
through an operator client that this gateway's CA issues. Run this on the server host
itself, not in a Coolify terminal, so the archive lands on the host:

```bash
docker ps --filter name=srg-gateway-api --format '{{.Names}}'
```

```bash
docker exec -i <that container> python -m app.enrolment.issue_operator --client-id aequoros-platform --organisation "AequorOS platform" > aequoros-operator.tar
```

The archive holds the operator's private key. The gateway keeps no copy, and the
issue is recorded in its audit log. This first issue also creates the gateway's CA.
Print the entry for the API's configuration:

```bash
tar -xOf aequoros-operator.tar aequoros-platform/gateway-operator-client.json
```

Set `GATEWAY_OPERATOR_CLIENTS` to that JSON on one line. It is the list, with the
square brackets.

## 6. Mutual TLS at the proxy

Banks and the operator authenticate with client certificates. Traefik (Coolify's
proxy) checks each certificate against the gateway's CA and forwards it to the API.

1. Copy the CA certificate to where the proxy reads it:

   ```bash
   docker exec <api container> cat /var/lib/gateway/keys/ca/gateway-ca.crt.pem | sudo tee /data/coolify/proxy/srg-gateway-ca.pem
   ```

2. Find the application's network and check that the proxy is attached to it:

   ```bash
   docker inspect <api container> -f '{{range $name, $n := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}'
   ```

   ```bash
   docker network inspect <that network> -f '{{range .IPAM.Config}}{{.Subnet}}{{end}} {{range .Containers}}{{.Name}} {{end}}'
   ```

   `coolify-proxy` must be in the list. Set `GATEWAY_TRUSTED_PROXIES` to the subnet
   printed. The API trusts the certificate header only from that network.

3. Servers → Proxy → **Dynamic Configurations** → add [`traefik-gateway.yml`](traefik-gateway.yml)
   as `srg-gateway.yaml`, with both `gateway.example.com` replaced by the gateway
   host. It uses Coolify's default entry points (`http`, `https`) and certificate
   resolver (`letsencrypt`); if your proxy configuration names them differently,
   match it.

4. Redeploy the gateway application, so the API reads `GATEWAY_OPERATOR_CLIENTS` and
   `GATEWAY_TRUSTED_PROXIES`.

5. Check the proxy reaches the API by its service name:

   ```bash
   docker exec coolify-proxy wget -qO- http://srg-gateway-api:8200/health/live
   ```

## 7. Connect AequorOS

1. Create the operator volume on the host **before** the next AequorOS deploy. The
   AequorOS compose declares it external, and a missing external volume fails the
   deploy:

   ```bash
   docker volume create aequoros-reporting-gateway-operator
   ```

2. Load the archive into it, owned by the user `risk-operator` runs as. Find that
   user with `docker exec <risk-operator container> id`.

   ```bash
   docker run --rm -i -v aequoros-reporting-gateway-operator:/d alpine:3.20 sh -c 'tar -x -C /d && chown -R <uid>:<gid> /d && chmod 700 /d/aequoros-platform && chmod 600 /d/aequoros-platform/client.key.pem' < aequoros-operator.tar
   ```

3. In the AequorOS application's environment variables:

   | Variable | Value |
   | --- | --- |
   | `REPORTING_GATEWAY_URL` | `https://<gateway host>` |
   | `REPORTING_GATEWAY_OPERATOR_DIR` | `/run/reporting-gateway-operator/aequoros-platform` |
   | `REPORTING_GATEWAY_ENVIRONMENT` | `production`: banks enrolled as `sandbox` file practice runs, which the portal does not list |

4. Redeploy AequorOS, then delete the archive from the host:

   ```bash
   shred -u aequoros-operator.tar
   ```

## 8. Verify

- `https://<gateway host>/health/ready` answers `{"status":"ok", …}` with the
  database, the schema and both keys `ok`.
- A certificate the gateway did not issue fails the TLS handshake. Create a
  self-signed one and present it:

  ```bash
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout self.key -out self.crt -subj /CN=test -days 1
  ```

  ```bash
  curl --cert self.crt --key self.key https://<gateway host>/health/live
  ```

  The handshake is refused.
- Staff console → the tenant → **AequorOS Reporting Gateway → Enrol on the gateway**,
  for one bank. The bank shows as connected.
- Sign in to the portal with an account from step 4.

## Backups

**The `gateway-keys` volume is irreplaceable.** It holds the receipt key, which every
receipt ever issued is verified against, the audit key, and the CA, which every bank
and operator certificate chains to. If it is lost, receipts can no longer be verified
and every institution must be enrolled again. Back it up after step 5 and after any
key rotation, encrypted and off the host:

```bash
docker run --rm -v <the gateway-keys volume>:/k alpine:3.20 tar -c -C /k . > gateway-keys.tar
```

Find the volume's full name with `docker volume ls | grep gateway-keys`. Back up the
database with your usual Postgres backups (`pg_dump`).

## Operating notes

- **One API replica.** The scheduler (audit-chain verification every 30 minutes,
  validation sweep) runs inside the API process, so a second replica runs every job
  twice.
- **Upgrades:** redeploy. Migrations run before the API starts.
- **The operator certificate** lasts two years. To replace it, issue a new client id
  (for example `aequoros-platform-2`) and add its entry to `GATEWAY_OPERATOR_CLIENTS`
  beside the old one. Then load it and point `REPORTING_GATEWAY_OPERATOR_DIR` at it.
  Remove the old entry last.
- **Bank certificates** are renewed by AequorOS before expiry (the console action
  and the scheduled check). Nothing on this server needs doing for them.

## Production

`GATEWAY_ENV=production` needs two things this deployment does not have:

1. A key provider and CA backed by a KMS or HSM, holding the receipt and audit keys
   and issuing client certificates. This is the Bank's key service in the end; an
   OpenBao Transit and PKI provider fits until then. The gateway's `KeyProvider` and
   `CertificateAuthority` interfaces are the seams.
2. `GATEWAY_API_URL` over https. The portal refuses plain http in production, so the
   internal URL in the compose file changes with it.
