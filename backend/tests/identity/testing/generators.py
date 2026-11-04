"""Scoped approval and access-request inputs for synthetic institutions."""

from hypothesis import strategies as st

TEMPLATE_CHECKPOINTS = ("draft", "pending_approval", "approved", "rejected", "superseded")
REQUEST_CHECKPOINTS = ("pending", "approved", "rejected", "revoked", "refiled", "shared")
ACCESS_ROUTES = ("/fx", "/liquidity/forecast", "/liquidity/monitoring")


def template_checkpoints() -> st.SearchStrategy[str]:
    """Governed histories; reporting/service/filing_workflow/templates.py and ICAAP equivalent."""
    return st.sampled_from(TEMPLATE_CHECKPOINTS)


def request_checkpoints() -> st.SearchStrategy[str]:
    """Request resolution/refiling; identity/api/manage_authorization.py access-request routes."""
    return st.sampled_from(REQUEST_CHECKPOINTS)


def engagement_reasons() -> st.SearchStrategy[str]:
    """Synthetic project reasons; identity/schemas/authorization.py caps reason_detail at 2000.

    Empty detail is valid for project_engagement; OTHER's required-detail rule
    is exercised separately. Include the cap and whitespace normalization.
    """
    return st.one_of(
        st.sampled_from(
            (
                "Quarter-end regulatory review",
                "Liquidity monitoring engagement",
                "FX exposure review",
            )
        ),
        st.sampled_from(("", " ", "Review " + "a" * 1993)),
    )
