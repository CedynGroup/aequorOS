"""Synthetic figures for seeded filings, in the shape of the AequorOS-aligned definitions.

Every payload passes its definition: on the official forms, each figure the workbook's
arithmetic reads gets a value and every total is computed from the workbook's own
formulas (the definition's ``sum`` rules); lines no rule reads stay blank, as unmapped
cells do in AequorOS. Itemised returns get a few labelled rows in each required
section and some of the optional ones. Values are decimal strings to two places in the
unit each line declares. Deterministic for a given random source.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from pydantic import JsonValue

from app.returns.catalogue_loader import CatalogueDocument
from app.returns.rules import PresentRule, SumRule

CENT = Decimal("0.01")
_ROW_LABELS: dict[str, tuple[str, ...]] = {
    "cet1": (
        "Paid-up ordinary shares",
        "Share premium",
        "Retained earnings",
        "Statutory reserve",
        "Regulatory deduction: intangible assets",
    ),
    "tier2": ("Subordinated term debt", "General provisions (booked)"),
    "credit_rwa": (
        "Claims on sovereigns",
        "Claims on banks",
        "Claims on corporates",
        "Retail claims",
        "Residential mortgages",
        "Off-balance-sheet exposures",
    ),
    "market_rwa": ("Interest rate risk", "Foreign exchange risk"),
    "operational_rwa": ("Basic indicator approach",),
    "capital_ratios": ("CET1 ratio", "Tier 1 ratio", "Capital adequacy ratio"),
    "hqla": (
        "Level 1: cash and central bank reserves",
        "Level 1: government securities",
        "Level 2A assets",
    ),
    "outflows": (
        "Retail deposits, stable",
        "Retail deposits, less stable",
        "Unsecured wholesale funding",
    ),
    "inflows": ("Performing loans", "Interbank placements"),
    "npl_levels": ("Gross loans", "Non-performing loans", "Provisions held"),
    "repricing_gap": ("Up to 1 month", "1 to 3 months", "3 to 12 months", "Over 1 year"),
    "eve_scenarios": ("Parallel up 200bp", "Parallel down 200bp"),
    "nop_by_currency": ("US dollar", "Euro", "Pound sterling"),
    "currency_positions": ("US dollar", "Euro", "Pound sterling"),
}
_TEXT: dict[str, str] = {
    "passed": "true",
    "side": "long",
    "within_single_limit": "true",
    "within_aggregate_limit": "true",
    "within_12m": "true",
    "breach": "false",
    "effective": "true",
    "status": "Within limit",
    "threshold_source": "Prudential directive",
    "connection": "Connected group",
    "basis_of_connection": "Common control",
    "exempt_basis": "Sovereign exposure",
    "ifrs9_stage": "Stage 1",
    "related_party": "No",
    "asset_type": "Government securities",
    "location": "Domestic",
}


def _text(value: Decimal) -> str:
    quantized = value.quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{quantized:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _number(unit: str, scale: float, rng: random.Random) -> Decimal:
    """A plausible figure in ``unit`` for an institution of size ``scale`` (GHS thousands)."""
    if unit == "percent":
        return Decimal(str(round(rng.uniform(2, 40), 2)))
    if unit == "count":
        return Decimal(rng.randint(0, 120))
    weight = rng.uniform(0.0005, 0.02)
    if unit == "millions":
        return Decimal(str(round(scale / 1000 * weight, 2)))
    if unit == "thousands":
        return Decimal(str(round(scale * weight, 2)))
    return Decimal(str(round(scale * 1000 * weight, 2)))


def _sums(doc: CatalogueDocument) -> dict[str, SumRule]:
    return {rule.target: rule for rule in doc.rules if isinstance(rule, SumRule)}


def _recompute(
    doc: CatalogueDocument, inputs: Mapping[str, Decimal | None]
) -> dict[str, Decimal | None]:
    """Every figure: the inputs as given, every total from its formula (blank as zero)."""
    sums = _sums(doc)
    values: dict[str, Decimal | None] = dict(inputs)

    def value(code: str) -> Decimal:
        if code not in values or (code in sums and code not in inputs and values[code] is None):
            rule = sums.get(code)
            if rule is None:
                values[code] = None
            else:
                values[code] = Decimal(0)  # cycle guard while computing
                values[code] = sum((value(t) for t in rule.terms), Decimal(0)) - sum(
                    (value(t) for t in rule.subtract), Decimal(0)
                )
        found = values[code]
        return found if found is not None else Decimal(0)

    for target in sums:
        values.pop(target, None)
    for target in sums:
        value(target)
    return values


def _fixed_lines(doc: CatalogueDocument) -> list[dict[str, JsonValue]]:
    out: list[dict[str, JsonValue]] = []
    for section in doc.sections:
        lines = section.get("lines")
        unit = str(section.get("unit") or "units")
        for line in lines if isinstance(lines, list) else []:
            if isinstance(line, dict):
                out.append({**line, "section_unit": unit})
    return out


def _fixed_payload(
    doc: CatalogueDocument, scale: float, rng: random.Random
) -> dict[str, Decimal | None]:
    sums = _sums(doc)
    inputs: dict[str, Decimal | None] = {}
    for line in _fixed_lines(doc):
        code = str(line["code"])
        if code in sums:
            continue
        if line.get("kind") != "number":
            inputs[code] = None
            continue
        unit = str(line.get("unit") or line["section_unit"])
        inputs[code] = _number(unit, scale, rng)
    return _recompute(doc, inputs)


def _itemised_payload(
    doc: CatalogueDocument, scale: float, rng: random.Random
) -> tuple[dict[str, str | None], dict[str, str]]:
    lines: dict[str, str | None] = {}
    labels: dict[str, str] = {}
    for section in doc.sections:
        if section.get("itemised") is not True:
            continue
        code = str(section["code"])
        if section.get("optional") is True and (code == "totals" or rng.random() < 0.5):
            continue
        title = str(section.get("title") or code)
        names = _ROW_LABELS.get(code, ())
        count = len(names) if names else rng.randint(2, 4)
        columns = section.get("columns")
        for index in range(1, count + 1):
            row = f"{index}"
            key = f"{code}:{row}"
            labels[key] = names[index - 1] if names else f"{title}, item {index}"
            for column in columns if isinstance(columns, list) else []:
                if not isinstance(column, dict):
                    continue
                name = str(column["key"])
                target = key if name == "value" else f"{key}:{name}"
                if column.get("kind") == "text":
                    lines[target] = _TEXT.get(name, f"{labels[key]}")
                else:
                    unit = str(column.get("unit") or "units")
                    lines[target] = _text(_number(unit, scale, rng))
    return lines, labels


def payload(doc: CatalogueDocument, scale: float, rng: random.Random) -> dict[str, JsonValue]:
    """A filing payload for ``doc`` that passes every rule of the definition."""
    if any(section.get("itemised") is True for section in doc.sections):
        lines, labels = _itemised_payload(doc, scale, rng)
        out_lines: dict[str, JsonValue] = dict(lines)
        out_labels: dict[str, JsonValue] = dict(labels)
        return {"currency": "GHS", "lines": out_lines, "labels": out_labels}
    figures = _fixed_payload(doc, scale, rng)
    fixed: dict[str, JsonValue] = {
        str(line["code"]): (
            None if figures.get(str(line["code"])) is None else _text(figures[str(line["code"])])  # type: ignore[arg-type]
        )
        for line in _fixed_lines(doc)
    }
    return {"currency": "GHS", "lines": fixed}


def _lines(body: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    raw = body.get("lines")
    return dict(raw) if isinstance(raw, dict) else {}


def corrupt(doc: CatalogueDocument, body: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Deliberately inconsistent figures, for the design's rejected filings.

    On the official forms a total stops matching its formula; on an itemised return a
    required section goes missing.
    """
    lines = _lines(body)
    present = next((rule for rule in doc.rules if isinstance(rule, PresentRule)), None)
    if present is not None:
        prefix = f"{present.section}:"
        kept = {k: v for k, v in lines.items() if not k.startswith(prefix)}
        labels = body.get("labels")
        kept_labels = (
            {k: v for k, v in labels.items() if not k.startswith(prefix)}
            if isinstance(labels, dict)
            else {}
        )
        return {**body, "lines": kept, "labels": kept_labels}
    for rule in doc.rules:
        if isinstance(rule, SumRule) and isinstance(lines.get(rule.target), str):
            current = Decimal(str(lines[rule.target]))
            lines[rule.target] = _text(current + Decimal(1000))
            return {**body, "lines": lines}
    return dict(body)


def reclassify(
    doc: CatalogueDocument, body: Mapping[str, JsonValue], amount: Decimal
) -> tuple[dict[str, JsonValue], tuple[str, str]]:
    """Move ``amount`` between two figures of one total; every total recomputed.

    Returns the amended payload and the two lines that changed (from, to).
    """
    lines = _lines(body)
    sums = _sums(doc)
    for rule in doc.rules:
        if not isinstance(rule, SumRule):
            continue
        inputs = [t for t in rule.terms if t not in sums and isinstance(lines.get(t), str)]
        candidates = [t for t in inputs if Decimal(str(lines[t])) > amount]
        if len(candidates) >= 2:
            source, target = candidates[0], candidates[1]
            figures: dict[str, Decimal | None] = {
                code: (Decimal(str(value)) if isinstance(value, str) else None)
                for code, value in lines.items()
                if code not in sums
            }
            figures[source] = (figures[source] or Decimal(0)) - amount
            figures[target] = (figures[target] or Decimal(0)) + amount
            recomputed = _recompute(doc, figures)
            moved: dict[str, JsonValue] = {
                code: (None if recomputed.get(code) is None else _text(recomputed[code]))  # type: ignore[arg-type]
                for code in lines
            }
            return {**body, "lines": moved}, (source, target)
    raise ValueError(f"{doc.code}: no total with two figures to reclassify between")
