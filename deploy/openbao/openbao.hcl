# OpenBao server configuration — signing keys and the attestation CA.
#
# TLS is terminated by the reverse proxy in front of this container, so the
# listener runs plaintext on the container network only. That is a deliberate
# choice with a condition attached: the proxy MUST be on this host, and the
# listener MUST NOT be published to the network. The application reaches
# OpenBao over HTTPS at its public hostname, which means a certificate from a
# public CA and no OPENBAO_CA_CERT to configure on the app side.
#
# The alternative — OpenBao terminating TLS itself with a private CA
# certificate — is supported by the application (OPENBAO_CA_CERT feeds httpx's
# verify) but has never been exercised against a real private CA. If you choose
# it, test the app's connection before cutting production over, because there is
# deliberately no "skip verification" switch to fall back on.

ui = false

# /openbao/file, not /openbao/data: the image runs as uid 100 and ships
# /openbao/{config,file,logs} owned by that user, but NO /openbao/data. A named
# volume mounted at a path the image does not contain is created root-owned, and
# the server dies on first boot with "permission denied" opening the bolt file.
# Verified against openbao 2.6.1.
storage "raft" {
  path    = "/openbao/file"
  node_id = "openbao-1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

# Both must be the address OTHER parties use, not the container's own. api_addr
# is what OpenBao puts in redirects and in the CRL/OCSP URLs the PKI engine
# stamps into every certificate — get it wrong and an external validator cannot
# fetch the revocation list for a filed return, years after the fact.
api_addr     = "https://bao.aequoros.com"
cluster_addr = "https://bao.aequoros.com:8201"

# No mlock setting: OpenBao 2.x DROPPED mlock support and refuses to start if
# `disable_mlock` appears here at all (verified against openbao 2.6.1 — the
# server exits with "OpenBao has dropped support for mlock"). Keeping key
# material out of swap is now the HOST's job: disable swap, or encrypt it. That
# step is in the runbook, and it is not optional — it is what replaces the
# guarantee this line used to make.

# Audit devices are NOT configured here — they are enabled after initialisation
# (see the runbook), because enabling an audit device that cannot write makes
# OpenBao refuse every request, including the ones you would need to fix it.
