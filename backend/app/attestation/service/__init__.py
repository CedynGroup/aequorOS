"""Attestation & e-signature (docs/attestation_esignature.md).

Layering, innermost first:

* ``digests``   — what a signature binds to (no I/O, no dependencies)
* ``identity``  — the permanent ``SGN-…`` signer identity
* ``policy``    — who must sign which return (configuration)
* ``workflow``  — the certification state machine and its guards
* ``tsa``       — RFC 3161 trusted time
* ``signers``   — the RawSigner port and its HSM/KMS/software backends
* ``pdf_signing`` — pyHanko PAdES signing of the return artifact
* ``stepup``    — step-up re-authentication and single-use authorisations
* ``verify``    — the five independent verification checks

pyHanko and ``cryptography`` are invoked server-side only and are never
user-facing.
"""
