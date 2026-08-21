from __future__ import annotations

from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, Numeric, String
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InstitutionType(TimestampMixin, Base):
    """Global reference registry: one row per BoG licence class (docs/sdi.md §1).

    ``banks.institution_type`` points here. This is the authoritative typed
    discriminator every future SDI scoping keys off — like ``jurisdictions`` it
    is DATA, not code: the coarse ``institution_class`` regime axis
    (``bank``/``sdi``), the return family, the capital regime, the exposure
    limits and the default module set all resolve from this row instead of
    hardcoded ``"bank"`` literals. Onboarding a new licence class is a data
    exercise (one registry row), and later SDI phases branch on these columns
    rather than re-deriving the classification in code.

    Deliberately NOT tenant-scoped (shared reference data, like ``jurisdictions``
    and parameter defaults; no RLS) and read-only through the API.

    Naming-collision warning (docs/sdi.md §1.3): ``institution_class`` here is
    the TENANT discriminator (what the institution IS). It is unrelated to the
    ``institution_class`` fact attribute in the BoG line-map DSL, which
    classifies the reporting bank's COUNTERPARTIES. Never conflate the two.
    """

    __tablename__ = "institution_types"
    __table_args__ = (
        CheckConstraint(
            "institution_class IN ('bank', 'sdi')",
            name="ck_institution_types_institution_class",
        ),
        CheckConstraint(
            "return_family IN ('bsd', 'sdi')",
            name="ck_institution_types_return_family",
        ),
        CheckConstraint(
            "capital_regime IN ('crd', 's29')",
            name="ck_institution_types_capital_regime",
        ),
    )

    # The specific BoG licence class (natural primary key; banks reference it
    # directly). e.g. ``universal_bank``, ``savings_and_loans``.
    type_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # The coarse regime axis derived from the licence class. Keeps the live
    # ``ParamLiquidityThreshold.institution_class`` column ('bank'|'sdi')
    # working unchanged: ``savings_and_loans -> sdi``.
    institution_class: Mapped[str] = mapped_column(String(8), nullable=False)
    # Which BoG return family the institution files ('bsd' universal-bank set,
    # 'sdi' the SDI family — obtained from BoG, not fabricated: docs/sdi.md §2.3).
    return_family: Mapped[str] = mapped_column(String(8), nullable=False)
    # Capital regime: 'crd' (Basel CRD, banks) vs 's29' (Act 930 s.29 floor, SDIs).
    capital_regime: Mapped[str] = mapped_column(String(8), nullable=False)
    # Per-obligor large-exposure limit as a % of Net Own Funds (bank 20, SDI 15
    # — Large Exposures Directive Sept 2025). Numeric, not int, so a future
    # licence class with a fractional limit needs no schema change.
    large_exposure_limit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    # Statutory single-obligor limit as a % of NOF (25 across classes — Act 930 s.62(1)).
    single_obligor_limit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    # Whether the LMTD Table 1 prudential ratios are BINDING compliance (SDIs,
    # LMTD ¶9) rather than monitoring benchmarks (banks). Later phases key the
    # liquidity threshold consequence on this; Phase A only records it.
    liquidity_binding: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The default scoped module set for this class (docs/sdi.md §3), stored as
    # data for later Phase-B module gating. Phase A populates it but wires NO
    # gating — every module stays visible until Phase B turns the nav arrays
    # into functions of this list.
    default_modules: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
