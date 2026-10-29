"""Synthetic matched-tenor pricing books, never customer or market observations."""

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import strategies as st

from app.domain.ftp.engine import CurvePoint, FtpCategory, FtpNmd, FtpProduct, build_curve
from app.policy.service.parameter_register import BANK_FTP_THRESHOLDS


@dataclass(frozen=True)
class FtpBook:
    points: tuple[CurvePoint, ...]
    products: tuple[FtpProduct, ...]
    nmds: tuple[FtpNmd, ...]


def policy(code: str) -> Decimal:
    """Bank policy, not a regulatory minimum: parameter_register.BANK_FTP_THRESHOLDS."""
    return Decimal(BANK_FTP_THRESHOLDS[code])


@st.composite
def ftp_books(draw: st.DrawFn) -> FtpBook:
    """Ascending synthetic curves where monotonicity is required by this fixture.

    Source: domain/ftp/engine.py CurvePoint, build_curve, validate_product_alignment;
    MONEY=4dp and RATIO_PCT=6dp, HALF_UP. Tenors and 20–30% synthetic yields
    surround tests/domain/test_ftp_engine.py's hand-built curve, not live quotes.
    The engine permits inverted curves; they remain covered by golden tests.
    NMD shares favour BANK_FTP_THRESHOLDS band endpoints and their 1ppm neighbours.
    """
    base = Decimal(draw(st.integers(2000, 3000))) / 100
    steps = draw(st.lists(st.integers(0, 50), min_size=4, max_size=4))
    points: list[CurvePoint] = []
    for index, tenor in enumerate((Decimal("0.003"), Decimal("0.5"), Decimal(2), Decimal(5))):
        base += Decimal(steps[index]) / 100
        liquidity = Decimal(index * 10)
        funding = Decimal(40 + index * 5)
        points.append(
            CurvePoint(
                str(tenor), tenor, base, liquidity, funding, base + (liquidity + funding) / 100
            )
        )
    curve = build_curve(points)
    products: list[FtpProduct] = []
    nmds: list[FtpNmd] = []
    floor = policy("ftp_min_product_margin_pct")
    categories: tuple[FtpCategory, ...] = ("asset", "liability")
    for index, category in enumerate(categories):
        tenor = draw(st.sampled_from(tuple(p.tenor_years for p in points)))
        ftp = curve.rate_at(tenor)
        # Whole-unit balances deliberately avoid sub-quantum scaling artefacts.
        balance = Decimal(draw(st.integers(100_000, 100_000_000)))
        margin = draw(
            st.one_of(
                st.sampled_from((floor - Decimal("0.000001"), floor, floor + Decimal("0.000001"))),
                st.decimals(-2, 5, places=6),
            )
        )
        cost = Decimal("0.3")
        customer = ftp + cost + margin if category == "asset" else ftp - cost - margin
        products.append(
            FtpProduct(
                f"synthetic_{index}",
                category,
                balance,
                tenor,
                customer,
                ftp,
                cost,
                Decimal(0),
                Decimal(0),
            )
        )
        low, high = policy("nmd_core_min_pct"), policy("nmd_core_max_pct")
        share = draw(
            st.one_of(
                st.sampled_from((low - Decimal("0.000001"), low, high, high + Decimal("0.000001"))),
                st.decimals(low, high, places=6),
            )
        )
        nmds.append(FtpNmd(f"synthetic_{index}", balance, share, 100 - share, tenor))
    return FtpBook(tuple(points), tuple(products), tuple(nmds))
