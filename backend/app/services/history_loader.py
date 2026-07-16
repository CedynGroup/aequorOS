"""Bulk-load the 10-year simulator parquet panels into canonical tables.

Reads the panels written by ``data/simulator`` and inserts them as canonical
records for one bank, then derives the module fact set for every month. This is
a seed/backfill path, deliberately separate from the validation-gated ingestion
pipeline: it inserts pre-validated canonical rows directly (``validation_status
= "accepted"``) via SQLAlchemy ``insertmanyvalues`` under the app role, with the
tenant RLS setting applied so ``WITH CHECK`` passes. It is NOT content-hash
idempotent — reset the bank first (``scripts/reset_uploaded_data.py``).

Dimensions (counterparties, products) are loaded once against the earliest
month; GL accounts and position snapshots are loaded per month; positions carry
one stable identity row per ``source_reference`` across all months. All rows are
stamped ``source_system = "API_PUSH"`` (the simulator's per-book systems are not
in the canonical vocabulary).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.ids import new_uuid7
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
)
from app.models.ingestion import IngestionBatch, LineageRecord
from app.services.fact_derivation import DerivationError, derive_facts

# Panel column contracts live with the simulator; import lazily to avoid a hard
# dependency when the package layout differs.
_ATTRIBUTE_COLUMNS = [
    "balance_ghs", "branch_id", "ecl_provision_ghs", "notional_ghs",
    "credit_conversion_factor", "credit_equivalent_ghs",
    "scheduled_principal_ghs", "interest_accrued_ghs", "months_on_book",
    "hedge_id", "instrument", "buy_currency", "sell_currency", "contract_rate",
    "mtm_ghs", "prospective_r2", "dollar_offset_ratio", "currency_pair",
    "swap_id", "direction", "pay_rate_pct", "receive_index", "tenor_years", "isin",
]
_SOURCE_SYSTEM = "API_PUSH"
_ADAPTER_VERSION = "history_sim_v1"


@dataclass
class LoadSummary:
    months: list[datetime.date] = field(default_factory=list)
    counterparties: int = 0
    products: int = 0
    gl_accounts: int = 0
    positions: int = 0
    snapshots: int = 0
    reference_rows: int = 0


def _iso(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _as_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    ts = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _clean(v):
    """NaN/NaT -> None; numpy scalars -> python; else passthrough."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if pd.api.types.is_scalar(v) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _rate_type(v):
    v = _clean(v)
    return v if v in ("FIXED", "FLOATING") else None


def _fk(id_map: dict, key) -> object | None:
    """Resolve a foreign-key id from a source-key map, tolerating NaN/None keys."""
    return id_map.get(_clean(key))


def _bulk_insert(session: Session, model, rows: list[dict], batch: int = 20_000) -> int:
    for i in range(0, len(rows), batch):
        session.execute(insert(model), rows[i : i + batch])
    return len(rows)


def _months_from_panels(panels_dir: Path) -> list[datetime.date]:
    root = panels_dir / "position_snapshots"
    months = []
    for p in sorted(root.glob("month=*")):
        months.append(datetime.date.fromisoformat(p.name.split("=", 1)[1]))
    return months


def load_history(  # noqa: PLR0913, PLR0912, PLR0915
    session: Session,
    org_id: UUID,
    bank_id: UUID,
    panels_dir: Path,
    *,
    months: list[datetime.date] | None = None,
    actor_user_id: UUID | None = None,
    batch_size: int = 20_000,
    log=print,
) -> LoadSummary:
    session.info["organization_id"] = org_id
    panels_dir = Path(panels_dir)
    all_months = _months_from_panels(panels_dir)
    if months is not None:
        wanted = set(months)
        all_months = [m for m in all_months if m in wanted]
    if not all_months:
        raise ValueError(f"No position_snapshots partitions found under {panels_dir}")
    summary = LoadSummary(months=all_months)
    common = dict(organization_id=org_id, bank_id=bank_id, created_by=actor_user_id)

    # 1) one batch + lineage per month
    batch_id: dict[datetime.date, UUID] = {}
    lineage_id: dict[datetime.date, UUID] = {}
    batch_rows, lineage_rows = [], []
    for m in all_months:
        bid, lid = new_uuid7(), new_uuid7()
        batch_id[m], lineage_id[m] = bid, lid
        batch_rows.append(dict(id=bid, organization_id=org_id, bank_id=bank_id,
                               source_system=_SOURCE_SYSTEM, adapter_version=_ADAPTER_VERSION,
                               extraction_mode="full", status="accepted", as_of_date=m,
                               created_by=actor_user_id))
        lineage_rows.append(dict(id=lid, organization_id=org_id, ingestion_batch_id=bid,
                                 operation_type="VALIDATION",
                                 operation_ref=f"history_simulator_v1/{m.isoformat()}"))
    _bulk_insert(session, IngestionBatch, batch_rows)
    _bulk_insert(session, LineageRecord, lineage_rows)
    first = all_months[0]
    prov = dict(ingestion_batch_id=batch_id[first], lineage_id=lineage_id[first],
                validation_status="accepted", source_system=_SOURCE_SYSTEM, **common)

    # 2) dimensions once (attached to the earliest month; joined by id thereafter)
    cp = pd.read_parquet(panels_dir / "dim_counterparties" / "part.parquet")
    cp_id: dict[str, UUID] = {}
    cp_rows = []
    for r in cp.itertuples(index=False):
        cid = new_uuid7()
        cp_id[r.counterparty_id] = cid
        cp_rows.append(dict(id=cid, as_of_date=first, source_reference=r.counterparty_id,
                            name=r.counterparty_name, counterparty_type=r.counterparty_type,
                            country_code=_clean(r.country), rating=_clean(r.credit_rating), **prov))
    summary.counterparties = _bulk_insert(session, CanonicalCounterparty, cp_rows)

    prod = pd.read_parquet(panels_dir / "dim_products" / "part.parquet")
    prod_id: dict[str, UUID] = {}
    prod_rows = []
    for r in prod.itertuples(index=False):
        pid = new_uuid7()
        prod_id[r.product_code] = pid
        prod_rows.append(dict(id=pid, as_of_date=first, source_reference=r.product_code,
                              product_code=r.product_code, name=r.product_name,
                              regulatory_category=_clean(r.regulatory_category),
                              risk_weight_code=None,
                              attributes={"risk_weight": _clean(r.risk_weight)}, **prov))
    summary.products = _bulk_insert(session, CanonicalProduct, prod_rows)

    # reference datasets that are static across months
    behavioral = pd.read_parquet(panels_dir / "behavioral_assumptions" / "part.parquet")
    units = pd.read_parquet(panels_dir / "dim_business_units" / "part.parquet")
    institution = pd.read_parquet(panels_dir / "dim_institution" / "part.parquet")
    yc = pd.read_parquet(panels_dir / "yield_curves_monthly" / "part.parquet")
    fxm = pd.read_parquet(panels_dir / "fx_rates_monthly" / "part.parquet")
    fxd = pd.read_parquet(panels_dir / "fx_rates_daily" / "part.parquet")
    fin = pd.read_parquet(panels_dir / "monthly_financials" / "part.parquet")
    capm = pd.read_parquet(panels_dir / "capital_structure_monthly" / "part.parquet")
    capm = capm.rename(columns={"as_of_date": "month"})
    for df in (yc, fxm, capm):
        df["month"] = pd.to_datetime(df["month"]).dt.date
    fxd["date"] = pd.to_datetime(fxd["date"]).dt.date
    fin["period_end"] = pd.to_datetime(fin["period_end"]).dt.date

    # 3-6) per month: GL, position identities, snapshots, reference rows
    pos_id: dict[str, UUID] = {}
    for m in all_months:
        bid, lid = batch_id[m], lineage_id[m]
        mprov = dict(ingestion_batch_id=bid, lineage_id=lid, validation_status="accepted",
                     source_system=_SOURCE_SYSTEM, **common)

        gl = pd.read_parquet(panels_dir / "gl_accounts" / f"month={m.isoformat()}" / "part.parquet")
        gl_id: dict[str, UUID] = {}
        gl_rows = []
        for r in gl.itertuples(index=False):
            gid = new_uuid7()
            gl_id[r.gl_code] = gid
            gl_rows.append(dict(id=gid, as_of_date=m, source_reference=r.gl_code,
                                account_code=r.gl_code, name=r.gl_name,
                                account_class=r.account_class, currency=_clean(r.currency),
                                balance=_clean(r.balance_ghs), **mprov))
        summary.gl_accounts += _bulk_insert(session, CanonicalGlAccount, gl_rows)

        snap_path = panels_dir / "position_snapshots" / f"month={m.isoformat()}" / "part.parquet"
        snap = pd.read_parquet(snap_path)
        for c in ("origination_date", "contractual_maturity", "next_repricing_date"):
            snap[c] = snap[c].map(_as_date)

        # new position identities
        identity_rows, snap_rows = [], []
        recs = snap.to_dict("records")
        for r in recs:
            sref = r["source_reference"]
            pid = pos_id.get(sref)
            if pid is None:
                pid = new_uuid7()
                pos_id[sref] = pid
                identity_rows.append(dict(
                    id=pid, as_of_date=m, source_reference=sref,
                    position_type=r["position_type"], currency=r["currency"],
                    origination_date=_as_date(r.get("origination_date")), **mprov))
            attrs = {}
            for col in _ATTRIBUTE_COLUMNS:
                v = _clean(r.get(col))
                if v is not None:
                    attrs[col] = _iso(v) if isinstance(v, (datetime.date, datetime.datetime)) else v
            snap_rows.append(dict(
                id=new_uuid7(), as_of_date=m, source_reference=sref, position_id=pid,
                counterparty_id=_fk(cp_id, r.get("counterparty_id")),
                product_id=_fk(prod_id, r.get("product_code")),
                gl_account_id=_fk(gl_id, r.get("gl_code")),
                balance=_clean(r.get("balance_ccy")), notional=_clean(r.get("notional_ccy")),
                interest_rate=_clean(r.get("interest_rate")),
                rate_type=_rate_type(r.get("rate_type")),
                rate_index=_clean(r.get("rate_index")), rate_spread=_clean(r.get("rate_spread")),
                contractual_maturity=_as_date(r.get("contractual_maturity")),
                next_repricing_date=_as_date(r.get("next_repricing_date")),
                ifrs9_stage=_clean(r.get("ifrs9_stage")), attributes=attrs, **mprov))
        if identity_rows:
            summary.positions += _bulk_insert(session, CanonicalPosition, identity_rows, batch_size)
        summary.snapshots += _bulk_insert(session, CanonicalPositionSnapshot, snap_rows, batch_size)

        summary.reference_rows += _load_reference_rows(
            session, m, bid, lid, org_id, bank_id,
            behavioral, units, institution, yc, fxm, fxd, fin, capm)

        log(f"  loaded {m}  positions+={len(identity_rows):>6}  snapshots+={len(snap_rows):>7}  "
            f"(total snapshots {summary.snapshots:,})")

    return summary


def _load_reference_rows(session, m, bid, lid, org_id, bank_id,  # noqa: PLR0913
                         behavioral, units, institution, yc, fxm, fxd, fin, capm) -> int:
    def month_recs(df):
        return df[df.month == m].drop(columns=["month"]).to_dict("records")

    hist = fxd[fxd.date <= m].sort_values("date").groupby("currency", group_keys=False).tail(250)
    trailing = fin[fin.period_end <= m].sort_values("period_end").tail(36)
    datasets: dict[str, list[dict]] = {
        "yield_curve": month_recs(yc),
        "fx_rates_current": month_recs(fxm),
        "fx_rates_historical": hist.to_dict("records"),
        "capital_structure": month_recs(capm),
        "behavioral_assumptions": behavioral.to_dict("records"),
        "historical_financials": trailing.to_dict("records"),
        "business_units": units.to_dict("records"),
        "institution": institution.to_dict("records"),
    }

    rows = []
    for kind, payloads in datasets.items():
        for idx, payload in enumerate(payloads, start=1):
            clean = {
                k: (_iso(v) if isinstance(v, (datetime.date, datetime.datetime)) else _clean(v))
                for k, v in payload.items()
            }
            rows.append(dict(id=new_uuid7(), organization_id=org_id, bank_id=bank_id,
                             ingestion_batch_id=bid, as_of_date=m, dataset_kind=kind,
                             row_index=idx, payload=clean,
                             source_reference=f"history_simulator_v1/{m.isoformat()}#{kind}!{idx}",
                             lineage_id=lid))
    return _bulk_insert(session, CanonicalReferenceRow, rows)


def derive_all_periods(session: Session, org_id: UUID, bank_id: UUID,  # noqa: PLR0913
                       months: list[datetime.date], *, actor_user_id: UUID | None = None,
                       log=print) -> dict[str, int]:
    session.info["organization_id"] = org_id
    ctx = TenantContext(organization_id=org_id, actor_user_id=actor_user_id)
    ok, failed = 0, 0
    for m in months:
        try:
            result = derive_facts(session, ctx, bank_id, m)
            session.commit()
            ok += 1
            warn = sum(len(g.warnings) for g in result.groups) if hasattr(result, "groups") else 0
            if warn or m == months[-1] or ok % 12 == 1:
                facts = getattr(result, "facts_created", "?")
                log(f"  derived {m}: {facts} facts, {warn} warnings")
        except DerivationError as exc:
            session.rollback()
            failed += 1
            log(f"  FAILED {m}: {exc}")
    return {"derived": ok, "failed": failed}
