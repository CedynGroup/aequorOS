"""The practice return catalogue: definitions derived from AequorOS's return templates.

``app/returns/catalogue/*.json`` is exported from AequorOS
(``backend/scripts/export_gateway_catalog.py``) and read here as data; nothing in the
gateway imports AequorOS. Each document describes one return the way the AequorOS
template does:

* **Fixed lines** (the official BoG forms): every declared input cell, coded as
  AequorOS codes it (``BSD2.R7.domestic``), and every workbook formula that is a plain
  sum or difference (``BSD2!D7``), with that formula as an arithmetic rule.
* **Itemised sections** (platform returns whose rows come from the institution's own
  data): rows coded ``<section>:<row>`` (other columns ``<section>:<row>:<column>``),
  each with its label in the payload's ``labels``, and a rule that every required
  section is reported.

Values are decimal strings in the unit the section declares, or ``null`` for a blank
cell. In ``local``, ``test`` and ``sandbox``, ``python -m app.returns.catalogue_loader``
publishes every document as a new version through the real draft and two-approver
workflow, and retires the earlier versions it replaces, so new filings of every
reporting date are checked against the aligned structure while filings already made
keep the version they were filed under. It refuses ``production``, where BoG publishes
its own definitions.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, append
from app.auth.staff import StaffPrincipal
from app.core.context import AppContext
from app.db.models import ReturnDefinition
from app.returns.rules import Rule, parse_rules
from app.returns.service import (
    DraftIn,
    approve,
    create_draft,
    definition,
    pending_draft,
    publish_baseline,
    published_versions,
    return_to_drafter,
    versions_of,
)

CATALOGUE_DIR = Path(__file__).parent / "catalogue"
FORMAT = "aequoros-gateway-return/1"
DECIMAL_PATTERN = r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"
TEXT_MAX = 4000
LABEL_MAX = 500

LineKind = Literal["number", "value", "text"]


@dataclass(frozen=True)
class CatalogueDocument:
    code: str
    name: str
    cadence: str
    applies_to: list[str]
    version: str
    first_reporting_date: date
    source: dict[str, JsonValue]
    sections: list[dict[str, JsonValue]]
    rules: list[Rule]


def _object(value: JsonValue, where: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} is not an object")
    return value


def _text(value: JsonValue, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} is not a string")
    return value


def parse_document(raw: JsonValue) -> CatalogueDocument:
    doc = _object(raw, "document")
    if doc.get("format") != FORMAT:
        raise ValueError(f"unsupported catalogue format {doc.get('format')!r}")
    code = _text(doc.get("code"), "code")
    applies = doc.get("applies_to")
    sections = doc.get("sections")
    rules = doc.get("rules")
    if (
        not isinstance(applies, list)
        or not isinstance(sections, list)
        or not isinstance(rules, list)
    ):
        raise ValueError(f"{code}: applies_to, sections and rules must be lists")
    return CatalogueDocument(
        code=code,
        name=_text(doc.get("name"), f"{code}.name"),
        cadence=_text(doc.get("cadence"), f"{code}.cadence"),
        applies_to=[_text(item, f"{code}.applies_to") for item in applies],
        version=_text(doc.get("version"), f"{code}.version"),
        first_reporting_date=date.fromisoformat(
            _text(doc.get("first_reporting_date"), f"{code}.first_reporting_date")
        ),
        source=_object(doc.get("source") or {}, f"{code}.source"),
        sections=[_object(section, f"{code}.sections") for section in sections],
        rules=parse_rules(rules),
    )


@cache
def documents() -> tuple[CatalogueDocument, ...]:
    """Every catalogue document, in file-name order."""
    return tuple(
        parse_document(cast(JsonValue, json.loads(path.read_text(encoding="utf-8"))))
        for path in sorted(CATALOGUE_DIR.glob("*.json"))
    )


def _value_schema(kind: str, title: str | None = None) -> dict[str, JsonValue]:
    schema: dict[str, JsonValue] = (
        {"type": ["string", "null"], "pattern": DECIMAL_PATTERN}
        if kind == "number"
        else {"type": ["string", "null"], "maxLength": TEXT_MAX}
    )
    if title is not None:
        schema["title"] = title
    return schema


def build_schema(doc: CatalogueDocument) -> dict[str, JsonValue]:
    """The version's JSON Schema 2020-12, with its sections and lines for display."""
    properties: dict[str, JsonValue] = {}
    required: list[JsonValue] = []
    patterns: dict[str, JsonValue] = {}
    label_patterns: dict[str, JsonValue] = {}
    sections_meta: list[JsonValue] = []
    lines_meta: list[JsonValue] = []
    for section in doc.sections:
        code = _text(section.get("code"), f"{doc.code} section code")
        title = _text(section.get("title"), f"{doc.code} section title")
        unit = _text(section.get("unit"), f"{doc.code} section unit")
        itemised = section.get("itemised") is True
        sections_meta.append(
            {
                "code": code,
                "title": title,
                "unit": unit,
                "itemised": itemised,
                "optional": section.get("optional") is True,
            }
        )
        prefix = re.escape(code)
        if itemised:
            columns = section.get("columns")
            for column in columns if isinstance(columns, list) else []:
                column_obj = _object(column, f"{doc.code} column")
                key = _text(column_obj.get("key"), f"{doc.code} column key")
                kind = _text(column_obj.get("kind"), f"{doc.code} column kind")
                pattern = (
                    f"^{prefix}:[^:]+$" if key == "value" else f"^{prefix}:[^:]+:{re.escape(key)}$"
                )
                patterns[pattern] = _value_schema(kind)
            label_patterns[f"^{prefix}:[^:]+$"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": LABEL_MAX,
            }
            continue
        lines = section.get("lines")
        for line in lines if isinstance(lines, list) else []:
            line_obj = _object(line, f"{doc.code} line")
            line_code = _text(line_obj.get("code"), f"{doc.code} line code")
            label = _text(line_obj.get("label"), f"{doc.code} line label")
            kind = _text(line_obj.get("kind"), f"{doc.code} line kind")
            properties[line_code] = _value_schema(kind, label)
            required.append(line_code)
            meta: dict[str, JsonValue] = {
                "code": line_code,
                "label": label,
                "total": line_obj.get("total") is True,
                "section": code,
                "kind": kind,
            }
            if isinstance(line_obj.get("unit"), str):
                meta["unit"] = line_obj.get("unit")
            lines_meta.append(meta)
    lines_schema: dict[str, JsonValue] = {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    if patterns:
        lines_schema["patternProperties"] = patterns
    labels_schema: dict[str, JsonValue] = {
        "type": "object",
        "additionalProperties": False,
        "patternProperties": label_patterns,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:srg:return:{doc.code}:{doc.version}",
        "title": f"{doc.code} {doc.name}",
        "type": "object",
        "additionalProperties": False,
        "required": ["currency", "lines"],
        "properties": {
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            "lines": lines_schema,
            "labels": labels_schema,
        },
        "x-srg-practice": True,
        "x-srg-source": doc.source,
        "x-srg-sections": sections_meta,
        "x-srg-lines": lines_meta,
    }


def _change_summary(doc: CatalogueDocument) -> str:
    template = doc.source.get("template")
    fidelity = doc.source.get("fidelity")
    return (
        f"Aligned with the AequorOS return template {template} ({fidelity}): the lines, labels, "
        "sections and units AequorOS files, with the template's own arithmetic as rules. "
        "Practice definition for local, test and sandbox use."
    )


def _register(
    session: Session, ctx: AppContext, doc: CatalogueDocument, position: int, now: datetime
) -> ReturnDefinition:
    row = definition(session, doc.code)
    changed: dict[str, JsonValue] = {}
    if row is None:
        row = ReturnDefinition(
            code=doc.code,
            name=doc.name,
            cadence=doc.cadence,
            applies_to=list(doc.applies_to),
            synthetic=True,
            auto_accept=False,
            position=position,
            created_at=now,
        )
        session.add(row)
        session.flush()
        action = "Registered return definition"
        changed = {"name": doc.name, "cadence": doc.cadence, "applies_to": list(doc.applies_to)}
    else:
        if row.name != doc.name:
            changed["name"] = doc.name
            row.name = doc.name
        if row.cadence != doc.cadence:
            changed["cadence"] = doc.cadence
            row.cadence = doc.cadence
        if list(row.applies_to) != doc.applies_to:
            changed["applies_to"] = list(doc.applies_to)
            row.applies_to = list(doc.applies_to)
        action = "Aligned return definition with the AequorOS template"
    if changed:
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=SYSTEM,
            category="configuration",
            at=now,
            action=action,
            object_type="return_definition",
            object_ref=doc.code,
            detail={**changed, "source": doc.source},
        )
    return row


def draft_of(doc: CatalogueDocument) -> DraftIn:
    """The draft that publishes ``doc`` as its own version."""
    return DraftIn.model_validate(
        {
            "version": doc.version,
            "change_summary": _change_summary(doc),
            "first_reporting_date": doc.first_reporting_date,
            "notice_days": 0,
            "schema": build_schema(doc),
            "rules": doc.rules,
        }
    )


def publish_catalogue_baseline(
    session: Session,
    ctx: AppContext,
    *,
    at: datetime | None = None,
    codes: Iterable[str] | None = None,
) -> list[str]:
    """Publish every catalogue document not yet published, as the catalogue baseline.

    For a real gateway's first start: the definitions are published by the AequorOS
    catalogue itself (:func:`app.returns.service.publish_baseline`), so no person is
    recorded as having drafted or approved them.
    """
    wanted = set(codes) if codes is not None else None
    done: list[str] = []
    for position, doc in enumerate(documents()):
        if wanted is not None and doc.code not in wanted:
            continue
        now = at or ctx.clock.now()
        row = _register(session, ctx, doc, position, now)
        existing = [v for v in versions_of(session, row.id) if v.version == doc.version]
        if any(v.status in ("published", "retired") for v in existing):
            continue
        publish_baseline(session, ctx, doc.code, draft_of(doc), at=now)
        done.append(f"{doc.code} {doc.version}")
    return done


def publish_catalogue(
    session: Session,
    ctx: AppContext,
    *,
    drafter: StaffPrincipal | None = None,
    approvers: tuple[StaffPrincipal, StaffPrincipal] | None = None,
    at: datetime | None = None,
    codes: Iterable[str] | None = None,
) -> list[str]:
    """Publish every catalogue document not yet published; ``[code version]`` done.

    Each goes through the real workflow: drafted by a returns policy officer and
    approved by a second policy officer and a senior supervisor (by default the
    development identities). Earlier published versions of the same return are retired.
    """
    if drafter is None or approvers is None:
        from app.seed.staff import sign_in  # development identities only

        drafter = sign_in(session, ctx, "easante")
        approvers = (sign_in(session, ctx, "madjei"), sign_in(session, ctx, "kowusu"))
    policy, supervision = approvers
    wanted = set(codes) if codes is not None else None
    done: list[str] = []
    for position, doc in enumerate(documents()):
        if wanted is not None and doc.code not in wanted:
            continue
        now = at or ctx.clock.now()
        row = _register(session, ctx, doc, position, now)
        existing = [v for v in versions_of(session, row.id) if v.version == doc.version]
        if any(v.status in ("published", "retired") for v in existing):
            continue
        waiting = pending_draft(session, row.id)
        if waiting is not None:
            return_to_drafter(
                session,
                ctx,
                policy if waiting.drafted_by != policy.subject else supervision,
                doc.code,
                waiting.version,
                "Superseded by the definition aligned with the AequorOS return template.",
            )
        create_draft(session, ctx, drafter, doc.code, draft_of(doc), at=now)
        approve(session, ctx, policy, doc.code, doc.version, at=now)
        approve(session, ctx, supervision, doc.code, doc.version, at=now)
        for other in published_versions(session, row.id):
            if other.version == doc.version:
                continue
            other.status = "retired"
            append(
                session,
                ctx.keys,
                ctx.clock,
                actor=SYSTEM,
                category="configuration",
                at=now,
                action=f"Retired {doc.code} {other.version}",
                object_type="return_version",
                object_ref=f"{doc.code} {other.version}",
                detail={
                    "superseded_by": doc.version,
                    "reason": "Replaced by the definition aligned with the AequorOS template.",
                },
            )
        done.append(f"{doc.code} {doc.version}")
    return done


def document(code: str) -> CatalogueDocument | None:
    return next((doc for doc in documents() if doc.code == code), None)


def main() -> int:
    from app.core.context import app_context
    from app.core.settings import get_settings
    from app.main import create_app

    settings = get_settings()
    if not settings.allows_seed:
        print(
            f"refusing: GATEWAY_ENV={settings.env}; BoG publishes production definitions itself",
            file=sys.stderr,
        )
        return 2
    ctx = app_context(create_app(settings, run_scheduler=False))
    with ctx.db.session() as session:
        done = publish_catalogue(session, ctx)
    print(f"published {len(done)} aligned definitions" + (f": {', '.join(done)}" if done else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
