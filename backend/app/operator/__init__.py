"""AequorOS operator control plane (docs/internal/developer.md).

A SEPARATE ASGI app (``app.operator.main:app``) for AequorOS staff: tenant
provisioning, cross-tenant operations visibility, and the audit plumbing they
require. Never mounted on the tenant API; never imports ``app.api.router``.
"""
