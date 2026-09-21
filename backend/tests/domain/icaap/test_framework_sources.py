"""Every citation in a framework is listed in its SOURCES manifest, and vice versa.

The manifest is how "nothing is invented" becomes checkable. A citation in the
JSON that no manifest row backs would be a paragraph somebody remembered; a
manifest row nothing cites would be a source that was read and then quietly
dropped. Both fail here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework

_SHA256_LENGTH = 64


@dataclass(frozen=True)
class Manifest:
    documents: dict[str, dict[str, str]]
    extractions: dict[str, dict[str, str]]
    citations: dict[str, dict[str, str]]


def _tables(text: str) -> dict[str, list[dict[str, str]]]:
    """Parse the file's Markdown tables: header, separator, then rows."""
    tables: dict[str, list[dict[str, str]]] = {}
    section = ""
    headers: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip().casefold()
            headers = None
            continue
        if not stripped.startswith("|"):
            headers = None
            continue
        cells = [cell.strip().strip("`") for cell in stripped.strip("|").split("|")]
        if headers is None:
            headers = cells
            continue
        if set("".join(cells)) <= {"-", ":"}:
            continue
        tables.setdefault(section, []).append(dict(zip(headers, cells, strict=False)))
    return tables


def _manifest(path: Path) -> Manifest:
    tables = _tables(path.read_text(encoding="utf-8"))
    return Manifest(
        documents={row["doc_id"]: row for row in tables.get("documents", [])},
        extractions={row["extract_id"]: row for row in tables.get("extractions", [])},
        citations={row["cite_id"]: row for row in tables.get("citations", [])},
    )


def _published() -> list[tuple[Framework, Manifest]]:
    pairs: list[tuple[Framework, Manifest]] = []
    for framework in registry.load_all():
        sources = registry.FRAMEWORK_ROOT / framework.jurisdiction.lower() / "SOURCES.md"
        assert sources.exists(), f"{framework.code} publishes no SOURCES.md"
        pairs.append((framework, _manifest(sources)))
    return pairs


PUBLISHED = _published()
IDS = [f"{framework.code} {framework.version}" for framework, _ in PUBLISHED]


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_every_citation_is_listed_in_the_manifest(framework: Framework, manifest: Manifest) -> None:
    used = {citation.cite_id for citation in framework.all_citations()}
    unlisted = sorted(used - set(manifest.citations))
    assert not unlisted, f"cited but not recorded as read: {unlisted}"


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_the_manifest_has_no_orphan_rows(framework: Framework, manifest: Manifest) -> None:
    used = {citation.cite_id for citation in framework.all_citations()}
    orphans = sorted(set(manifest.citations) - used)
    assert not orphans, f"recorded as read but cited nowhere: {orphans}"


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_every_row_resolves_to_a_document_and_an_extraction(
    framework: Framework, manifest: Manifest
) -> None:
    for cite_id, row in manifest.citations.items():
        assert row["doc_id"] in manifest.documents, cite_id
        extract = manifest.extractions.get(row["extract"])
        assert extract is not None, cite_id
        assert extract["doc_id"] == row["doc_id"], cite_id
        assert cite_id == f"{row['doc_id']}:{row['ref']}"
    declared = {document.id for document in framework.documents}
    assert set(manifest.documents) >= declared


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_a_document_read_from_a_held_pdf_records_its_fingerprint(
    framework: Framework, manifest: Manifest
) -> None:
    """A "pending" sha256 is only honest for a document nobody has read."""
    _ = framework
    from_local_pdf = {
        row["doc_id"] for row in manifest.citations.values() if row["text_status"] == "local_pdf"
    }
    for doc_id, document in manifest.documents.items():
        digest = document["sha256"]
        if doc_id in from_local_pdf:
            assert len(digest) == _SHA256_LENGTH, doc_id
            assert digest != "pending", doc_id
        elif digest != "pending":
            assert len(digest) == _SHA256_LENGTH, doc_id


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_a_sourced_section_rests_on_fully_read_paragraphs(
    framework: Framework, manifest: Manifest
) -> None:
    """A section may only claim "sourced" if its items cite text read in full.

    Its own heading is the exception: the heading is all that is needed to print
    the section title, and it is recorded as such.
    """
    weak = {"partial_extract", "heading_extract"}
    for section in framework.sections:
        if section.source_status != "sourced":
            continue
        for item in section.requirements:
            for citation in item.citations:
                row = manifest.citations[citation.cite_id]
                assert row["text_status"] not in weak, f"{section.key}/{item.id}"


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_the_manifest_agrees_with_the_documents_own_status(
    framework: Framework, manifest: Manifest
) -> None:
    for document in framework.documents:
        assert manifest.documents[document.id]["status"] == document.status


@pytest.mark.parametrize(("framework", "manifest"), PUBLISHED, ids=IDS)
def test_unrecovered_paragraphs_are_recorded_rather_than_filled_in(
    framework: Framework, manifest: Manifest
) -> None:
    """D-006: what has not been read is written down, so nobody back-fills it."""
    _ = manifest
    if all(section.source_status == "sourced" for section in framework.sections):
        return
    sources = registry.FRAMEWORK_ROOT / framework.jurisdiction.lower() / "SOURCES.md"
    text = sources.read_text(encoding="utf-8")
    assert "## Not yet recovered" in text
