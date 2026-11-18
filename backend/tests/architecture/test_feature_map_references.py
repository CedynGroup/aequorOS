"""The feature map names only routes, pages, test ids and files that still exist.

Why this exists
---------------
``docs/feature_map.md`` is a catalogue read by people and by the verification skill.
It rots silently: a renamed route or a moved page leaves an entry that still reads
well and now points at nothing, and nobody finds out until a verifier follows it.

What it checks
--------------
* Every token in an entry's ``Depends on`` line resolves against the real sources:
  tenant and operator API routes against the applications' registered routes, page
  paths against the Next.js ``app`` trees of the dashboard, console and marketing
  site, ``data-testid`` strings against their component sources, and file paths
  against the files Git tracks.
* Every Markdown link and every backticked repository path anywhere in the map
  points at a tracked file or directory, and every ``#anchor`` matches a heading.
* Every public API router group and every user-facing page group is either covered
  by an entry or listed under ``Not mapped`` with a reason, and a ``Not mapped``
  line that no longer matches anything is stale.

How an entry is written
-----------------------
A ``Depends on`` line is a list of labelled clauses separated by semicolons. A
label is ``tenant API``, ``operator API``, ``pages``, ``test ids`` or ``files``,
followed by backticked tokens. API tokens may start with an HTTP method and end in
``/*`` to name a whole subtree; tenant paths omit the ``/api/v1`` prefix. Page
paths are relative to the surface the entry sits under. ``{name}`` and ``[name]``
both stand for a path parameter.

Limits, stated rather than implied
----------------------------------
It proves a name still exists, not that the recipe around it still works, and a
group counts as covered when any entry names any path under it. Git is the source
of file truth so a file that is only ignored locally cannot hide a broken link;
the check therefore needs a Git checkout, which CI always has.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import pytest
from fastapi.routing import APIRoute
from starlette.applications import Starlette

from app.core.config import get_operator_settings, get_settings
from app.main import create_app
from app.operator.main import create_operator_app
from tests.support.route_authorization import registered_endpoints

REPO: Final = Path(__file__).parents[3]
MAP_FILE: Final = "docs/feature_map.md"

#: The map's surfaces: the heading each sits under and where its sources live.
API_SECTIONS: Final = {"Tenant API": "tenant", "Staff operator API": "operator"}
UI_SECTIONS: Final = {
    "Staff console": ("console/app", "console"),
    "Bank dashboard": ("backend/dashboard/app", "backend/dashboard"),
    "Marketing site": ("frontend/app", "frontend"),
}
NOT_MAPPED_HEADING: Final = "Not mapped"
#: Not mapped sub-headings, one per surface, in the same words as the section titles.
NOT_MAPPED_SURFACES: Final = (*API_SECTIONS, *UI_SECTIONS)

#: Served by the framework for every FastAPI app, so not features of the product.
FRAMEWORK_PATHS: Final = frozenset({"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"})
TENANT_PREFIX: Final = "/api/v1"
API_LABELS: Final = {"tenant API": "tenant", "operator API": "operator"}
LABELS: Final = (*API_LABELS, "pages", "test ids", "files")
ROOTS: Final = (
    "backend/",
    "console/",
    "frontend/",
    "docs/",
    "packages/",
    ".agents/",
    ".github/",
    "deploy/",
)
PAGE_FILES: Final = re.compile(r"(?:page|route)\.(?:tsx?|jsx?)$")
METADATA_PAGES: Final = {
    "sitemap": "/sitemap.xml",
    "robots": "/robots.txt",
    "opengraph-image": "/opengraph-image",
}
SOURCE_SUFFIXES: Final = (".ts", ".tsx", ".js", ".jsx", ".mjs")
TEST_ID_ATTRIBUTE: Final = re.compile(
    r"""(?<!\S)data-testid\s*=\s*(\{\s*)?(["'])([\w-]+)\2(?(1)\s*\})(?=\s|/|>)"""
    r"""|/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'"""
)
TEST_PATH: Final = re.compile(r"(?:^|/)(?:tests?|e2e|node_modules|\.next)/|\.(?:spec|test)\.")
METHODS: Final = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class Sources:
    """Everything a map is resolved against, so a synthetic tree can stand in for the repository."""

    routes: Mapping[str, Mapping[str, frozenset[str]]]  # "tenant"/"operator" -> path -> methods
    pages: Mapping[str, frozenset[str]]  # UI section -> normalized page paths
    operator_groups: Mapping[tuple[str, str], frozenset[str]]  # path and method -> router tags
    test_ids: Mapping[str, frozenset[str]]  # UI section -> literal data-testid values
    tracked: frozenset[str]  # repository-relative files Git tracks
    read_text: Callable[[str], str | None]  # a tracked file's text, for anchors


@dataclass(frozen=True)
class Entry:
    section: str
    heading: str
    line: int
    depends: str | None


def normalize(path: str) -> str:
    """Treat ``{id}``, ``[id]`` and ``[...rest]`` alike and ignore a trailing slash."""
    path = re.sub(r"\[\.\.\.[^\]]*\]|\[[^\]]*\]|\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower-case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


@dataclass
class _Draft:
    section: str
    heading: str
    line: int
    parts: list[str] | None = None
    open: bool = False


def parse_map(text: str) -> tuple[list[Entry], dict[str, list[tuple[int, str]]], list[str]]:
    """Split the map into entries, ``Not mapped`` bullets per surface, and every heading."""
    entries: list[Entry] = []
    not_mapped: dict[str, list[tuple[int, str]]] = {}
    headings: list[str] = []
    section = ""
    sub = ""
    draft: _Draft | None = None

    def finish() -> None:
        if draft is not None:
            depends = " ".join(draft.parts).strip() if draft.parts is not None else None
            entries.append(Entry(draft.section, draft.heading, draft.line, depends))

    for number, line in enumerate(text.splitlines(), start=1):
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            finish()
            draft = None
            level, title = len(heading.group(1)), heading.group(2).strip()
            headings.append(title)
            if level == 2:
                section, sub = title, ""
            elif level == 3 and section == NOT_MAPPED_HEADING:
                sub = title
            elif level == 3:
                draft = _Draft(section, title, number)
        elif section == NOT_MAPPED_HEADING:
            if line.startswith("- "):
                not_mapped.setdefault(sub, []).append((number, line))
            elif line.startswith("  ") and not_mapped.get(sub):
                first, last = not_mapped[sub][-1]
                not_mapped[sub][-1] = (first, f"{last} {line.strip()}")
        elif draft is not None:
            if line.startswith("- **Depends on**:"):
                draft.parts = [line.removeprefix("- **Depends on**:").strip()]
                draft.open = True
            elif (
                draft.open
                and draft.parts is not None
                and line.strip()
                and not line.startswith("- ")
            ):
                draft.parts.append(line.strip())
            else:
                draft.open = False
    finish()
    return entries, not_mapped, headings


def clauses(depends: str) -> tuple[list[tuple[str, list[str]]], bool]:
    """Return (label, tokens) clauses and whether any text before the first label held tokens."""
    marks = list(re.finditer(rf"(?:^|(?<=[\s;,.]))({'|'.join(LABELS)})(?=\s+`)", depends))
    if not marks:
        return [], bool(re.search(r"`[^`]+`", depends))
    lead = depends[: marks[0].start()]
    out: list[tuple[str, list[str]]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(depends)
        out.append((mark.group(1), re.findall(r"`([^`]+)`", depends[mark.end() : end])))
    return out, bool(re.search(r"`[^`]+`", lead))


def split_method(token: str) -> tuple[str | None, str]:
    head, _, rest = token.partition(" ")
    return (head, rest.strip()) if head in METHODS and rest else (None, token)


def api_path(token: str, api: str) -> str:
    path = normalize(token)
    if api == "tenant" and not path.startswith("/api/"):
        path = normalize(TENANT_PREFIX + path)
    return path


def tenant_group(path: str) -> str:
    """The router group of a normalized tenant path; bank-scoped paths group by feature."""
    if path.startswith(TENANT_PREFIX + "/"):
        segments = path.removeprefix(TENANT_PREFIX).split("/")[1:]
        if segments[0] == "banks" and len(segments) >= 3 and segments[1] == "{}":
            return f"/banks/{{}}/{segments[2]}"
        return "/" + segments[0]
    return "/" + "/".join(path.split("/")[1:3])


def page_group(path: str) -> str:
    segments = path.split("/")[1:]
    if segments[0] == "api" and len(segments) > 1:
        return f"/api/{segments[1]}"
    return "/" + segments[0] if segments[0] else "/"


def display_group(group: str) -> str:
    return group.replace("{}", "{bank_id}")


def matching_routes(
    token: str, api: str, routes: Mapping[str, frozenset[str]]
) -> Iterator[tuple[str, str]]:
    method, raw = split_method(token)
    wildcard = raw.endswith("/*")
    path = api_path(raw.removesuffix("/*"), api)
    for known, methods in routes.items():
        if known == path or (wildcard and known.startswith(path + "/")):
            yield from ((known, verb) for verb in methods if method is None or method == verb)


def route_exists(token: str, api: str, routes: Mapping[str, frozenset[str]]) -> bool:
    return next(matching_routes(token, api, routes), None) is not None


def page_exists(token: str, pages: frozenset[str]) -> bool:
    wildcard = token.endswith("/*")
    path = normalize(token.removesuffix("/*"))
    return any(
        page == path or (wildcard and page.startswith(path.rstrip("/") + "/")) for page in pages
    )


def entry_errors(entry: Entry, sources: Sources) -> Iterator[str]:
    where = f"{MAP_FILE}:{entry.line} '{entry.heading}'"
    if entry.depends is None:
        yield f"{where}: no Depends on line"
        return
    parsed, unlabelled = clauses(entry.depends)
    if unlabelled or not parsed:
        yield f"{where}: Depends on tokens need a label ({', '.join(LABELS)})"
    for label, tokens in parsed:
        if not tokens:
            yield f"{where}: '{label}' lists nothing"
        for token in tokens:
            if (message := token_error(label, token, entry.section, sources)) is not None:
                yield f"{where}: {message}"


def token_error(label: str, token: str, section: str, sources: Sources) -> str | None:
    if label in API_LABELS:
        api = API_LABELS[label]
        found = route_exists(token, api, sources.routes.get(api, {}))
        return None if found else f"{label} route `{token}` is not registered"
    if label == "files":
        return None if token in sources.tracked else f"file `{token}` is not tracked"
    if section not in UI_SECTIONS:
        return f"{label} `{token}` outside a UI surface"
    if label == "pages":
        found = page_exists(token, sources.pages.get(section, frozenset()))
        return None if found else f"page `{token}` is not under the {section} app tree"
    found = token in sources.test_ids.get(section, frozenset())
    return None if found else f"test id `{token}` appears in no {section} component source"


def link_errors(text: str, headings: list[str], sources: Sources) -> Iterator[str]:
    anchors = {slug(title) for title in headings}
    base = PurePosixPath(MAP_FILE).parent
    directories = {str(parent) for path in sources.tracked for parent in PurePosixPath(path).parents}
    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{MAP_FILE}:{number}"
        for target in re.findall(r"\]\(([^)\s]+)\)", line):
            if re.match(r"[a-z]+:", target):
                continue
            path, _, anchor = target.partition("#")
            if not path:
                if anchor not in anchors:
                    yield f"{where}: anchor #{anchor} matches no heading"
                continue
            resolved = posixpath.normpath(posixpath.join(str(base), path))
            if resolved not in sources.tracked:
                yield f"{where}: link target {target} is not a tracked file"
                continue
            if anchor and resolved.endswith(".md"):
                body = sources.read_text(resolved) or ""
                titles = re.findall(r"^#{1,6}\s+(.*)$", body, flags=re.MULTILINE)
                if anchor not in {slug(title) for title in titles}:
                    yield f"{where}: anchor #{anchor} matches no heading in {resolved}"
        for token in re.findall(r"`([^`]+)`", line):
            looks_like_path = token.startswith(ROOTS)
            if (
                looks_like_path
                and not re.search(r"[*{}\[\]<> ]", token)
                and token.rstrip("/") not in sources.tracked
                and token.rstrip("/") not in directories
            ):
                yield f"{where}: path `{token}` is not tracked"


def covered_groups(entries: list[Entry], sources: Sources) -> dict[str, set[str]]:
    """Groups any entry names: an API group is covered by its own entry or by a UI entry's read."""
    covered: dict[str, set[str]] = {}
    for entry in entries:
        for label, tokens in clauses(entry.depends or "")[0]:
            for token in tokens:
                path = token.removesuffix("/*")
                if label in API_LABELS:
                    api = API_LABELS[label]
                    groups = covered.setdefault(api, set())
                    if api == "tenant":
                        groups.add(tenant_group(api_path(split_method(path)[1], api)))
                    else:
                        for match in matching_routes(token, api, sources.routes.get(api, {})):
                            groups.update(sources.operator_groups.get(match, ()))
                elif label == "pages":
                    covered.setdefault(entry.section, set()).add(page_group(normalize(path)))
    return covered


def existing_groups(sources: Sources) -> dict[str, set[str]]:
    existing = {
        "tenant": {
            tenant_group(path)
            for path in sources.routes.get("tenant", {})
            if path not in FRAMEWORK_PATHS
        },
        "operator": {tag for tags in sources.operator_groups.values() for tag in tags},
    }
    for section in UI_SECTIONS:
        existing[section] = {page_group(path) for path in sources.pages.get(section, frozenset())}
    return existing


def not_mapped_groups(lines: list[tuple[int, str]]) -> tuple[dict[str, int], list[str]]:
    """The groups a surface lists as not mapped, with a message for any malformed line."""
    listed: dict[str, int] = {}
    problems: list[str] = []
    for number, line in lines:
        head, _, reason = line.partition(":")
        tokens = re.findall(r"`([^`]+)`", head)
        if not tokens or not reason.strip():
            problems.append(
                f"{MAP_FILE}:{number}: a Not mapped line is backticked groups, a colon, a reason"
            )
        listed.update({normalize(token): number for token in tokens})
    return listed, problems


def coverage_errors(
    entries: list[Entry], not_mapped: dict[str, list[tuple[int, str]]], sources: Sources
) -> Iterator[str]:
    covered = covered_groups(entries, sources)
    titles = {"tenant": "Tenant API", "operator": "Staff operator API"}
    for key, groups in sorted(existing_groups(sources).items()):
        surface = titles.get(key, key)
        listed, problems = not_mapped_groups(not_mapped.get(surface, []))
        yield from problems
        claimed = covered.get(key, set())
        for group in sorted(groups - claimed - set(listed)):
            yield f"{surface}: {display_group(group)} is neither covered nor in Not mapped"
        for group, number in sorted(listed.items()):
            where = f"{MAP_FILE}:{number}: Not mapped lists {display_group(group)}"
            if group not in groups:
                yield f"{where}, which no longer exists"
            elif group in claimed:
                yield f"{where}, which an entry covers"
    for surface in not_mapped:
        if surface not in NOT_MAPPED_SURFACES:
            yield f"{MAP_FILE}: Not mapped has an unknown surface heading '{surface}'"


def map_errors(text: str, sources: Sources) -> list[str]:
    entries, not_mapped, headings = parse_map(text)
    errors: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.section in (*API_SECTIONS, *UI_SECTIONS):
            if entry.heading in seen:
                errors.append(f"{MAP_FILE}:{entry.line}: duplicate heading '{entry.heading}'")
            seen.add(entry.heading)
            errors.extend(entry_errors(entry, sources))
    scoped = [entry for entry in entries if entry.section in (*API_SECTIONS, *UI_SECTIONS)]
    errors.extend(link_errors(text, headings, sources))
    errors.extend(coverage_errors(scoped, not_mapped, sources))
    return errors


# --- real sources -----------------------------------------------------------------


def tracked_files() -> frozenset[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, text=True
    )
    return frozenset(name for name in result.stdout.split("\0") if name)


def page_path(app_root: str, file: str) -> str | None:
    relative = PurePosixPath(file).relative_to(app_root)
    stem = relative.name.rsplit(".", 1)[0]
    segments = [part for part in relative.parent.parts if not re.fullmatch(r"\(.*\)", part)]
    if PAGE_FILES.search(relative.name):
        return normalize("/" + "/".join(segments))
    if not relative.parent.parts and stem in METADATA_PAGES:
        return METADATA_PAGES[stem]
    return None


def literal_test_ids(source: str) -> Iterator[str]:
    """Read literal selector declarations. A declaration can exist without rendering."""
    for match in TEST_ID_ATTRIBUTE.finditer(source):
        if value := match[3]:
            yield value


def real_sources(applications: Mapping[str, Starlette]) -> Sources:
    tracked = tracked_files()
    endpoints, problems = registered_endpoints(applications, {})
    assert not problems, problems
    routes: dict[str, dict[str, set[str]]] = {"tenant": {}, "operator": {}}
    for endpoint in endpoints:
        application, method, path = endpoint.key
        routes[application].setdefault(normalize(path), set()).add(method)
    operator_groups = {
        (normalize(route.path), method): frozenset(str(tag) for tag in route.tags)
        or frozenset({normalize(route.path)})
        for route in applications["operator"].routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    pages: dict[str, frozenset[str]] = {}
    test_ids: dict[str, frozenset[str]] = {}
    for section, (app_root, source_root) in UI_SECTIONS.items():
        found = {page_path(app_root, name) for name in tracked if name.startswith(app_root + "/")}
        pages[section] = frozenset(path for path in found if path is not None)
        test_ids[section] = frozenset(
            test_id
            for name in tracked
            if name.startswith(source_root + "/")
            and name.endswith(SOURCE_SUFFIXES)
            and not TEST_PATH.search(name)
            for test_id in literal_test_ids((REPO / name).read_text(encoding="utf-8", errors="replace"))
        )
    return Sources(
        routes={
            api: {path: frozenset(methods) for path, methods in rows.items()}
            for api, rows in routes.items()
        },
        pages=pages,
        operator_groups=operator_groups,
        test_ids=test_ids,
        tracked=tracked,
        read_text=lambda name: (
            (REPO / name).read_text(encoding="utf-8") if name in tracked else None
        ),
    )


@pytest.fixture(scope="module")
def applications() -> Iterator[dict[str, Starlette]]:
    mp = pytest.MonkeyPatch()
    mp.setenv("OPERATOR_DATABASE_URL", "")
    mp.setenv("OPERATOR_DEV_AUTH_ENABLED", "0")
    get_operator_settings.cache_clear()
    assert not get_settings().worker.run_inprocess_worker
    try:
        yield {"tenant": create_app(), "operator": create_operator_app()}
    finally:
        get_operator_settings.cache_clear()
        mp.undo()


def test_the_feature_map_names_only_what_still_exists(
    applications: dict[str, Starlette],
) -> None:
    text = (REPO / MAP_FILE).read_text(encoding="utf-8")
    errors = map_errors(text, real_sources(applications))
    assert not errors, "\n" + "\n".join(errors)


# --- self-proof on a synthetic tree ----------------------------------------------

SYNTHETIC: Final = Sources(
    routes={
        "tenant": {
            "/api/v1/banks/{}/widgets": frozenset({"GET", "POST"}),
            "/api/v1/banks/{}/widgets/{}": frozenset({"GET"}),
            "/api/v1/legacy": frozenset({"GET"}),
            "/api/health/live": frozenset({"GET"}),
            "/docs": frozenset({"GET"}),
        },
        "operator": {"/operator/v1/tenants": frozenset({"GET", "POST"})},
    },
    pages={
        "Staff console": frozenset({"/", "/tenants", "/tenants/{}"}),
        "Bank dashboard": frozenset({"/widgets", "/api/health"}),
        "Marketing site": frozenset({"/", "/sitemap.xml"}),
    },
    operator_groups={
        ("/operator/v1/tenants", "GET"): frozenset({"operator-tenants"}),
        ("/operator/v1/tenants", "POST"): frozenset({"operator-provisioning"}),
    },
    test_ids={
        "Staff console": frozenset(
            literal_test_ids(
                "<Tag data-testid='console-badge' />"
                "<Tag data-testid='console-gone-extra' />"
                "{/* <Tag data-testid='console-gone' /> */}"
            )
        ),
        "Bank dashboard": frozenset(literal_test_ids('<Tag data-testid="widget-total" />')),
    },
    tracked=frozenset({"docs/feature_map.md", "backend/scripts/tool.py", "docs/other.md"}),
    read_text=lambda name: "# Other\n\n## Part one\n" if name == "docs/other.md" else None,
)

CLEAN_MAP: Final = """# Map

## Tenant API

### Widgets

Lists widgets. Tools are under `backend/scripts/` and `backend/scripts/{tool}/`.

- **Reach**: signed in.
- **Depends on**: tenant API `GET /banks/{bank_id}/widgets`, `/banks/{bank_id}/widgets/{id}`;
  files `backend/scripts/tool.py`.

### Health

- **Depends on**: tenant API `/api/health/live`.

## Staff operator API

### Tenants

- **Depends on**: operator API `GET /operator/v1/tenants`.

## Staff console

### Console tenants

See [Widgets](#widgets) and [other](other.md#part-one).

- **Depends on**: pages `/`, `/tenants/{orgId}`; test ids `console-badge`;
  operator API `GET /operator/v1/tenants`.

## Bank dashboard

### Dashboard widgets

- **Depends on**: pages `/widgets`, `/api/health/*`; test ids `widget-total`.

## Marketing site

### Marketing pages

- **Depends on**: pages `/`, `/sitemap.xml`.

## Not mapped

### Tenant API

- `/legacy`: kept for an old client.

### Staff operator API

- `operator-provisioning`: creates an organization.
"""


def test_a_current_map_passes() -> None:
    assert map_errors(CLEAN_MAP, SYNTHETIC) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("GET /banks/{bank_id}/widgets`", "GET /banks/{bank_id}/gadgets`", "route"),
        ("`GET /banks/{bank_id}/widgets`", "`DELETE /banks/{bank_id}/widgets`", "route"),
        ("pages `/`, `/tenants/{orgId}`", "pages `/`, `/tenant/{orgId}`", "page"),
        ("test ids `console-badge`", "test ids `console-gone`", "test id"),
        ("`backend/scripts/`", "`backend/missing/`", "not tracked"),
        ("`backend/scripts/tool.py`", "`backend/scripts/moved.py`", "not tracked"),
        ("[other](other.md#part-one)", "[other](other.md#part-two)", "matches no heading"),
        ("[Widgets](#widgets)", "[Widgets](#gadgets)", "matches no heading"),
        ("[other](other.md#part-one)", "[other](gone.md)", "not a tracked file"),
        ("tenant API `/api/health/live`", "`/api/health/live`", "need a label"),
    ],
)
def test_a_stale_reference_is_reported(old: str, new: str, expected: str) -> None:
    assert old in CLEAN_MAP
    errors = map_errors(CLEAN_MAP.replace(old, new, 1), SYNTHETIC)
    assert any(expected in error for error in errors), errors


def test_an_unclaimed_group_must_be_covered_or_listed_with_a_reason() -> None:
    unclaimed = CLEAN_MAP.replace("- `/legacy`: kept for an old client.", "").replace(
        "- `operator-provisioning`: creates an organization.", ""
    )
    errors = map_errors(unclaimed, SYNTHETIC)
    assert any("/legacy is neither covered" in e for e in errors)
    assert any("operator-provisioning is neither covered" in e for e in errors)
    no_reason = CLEAN_MAP.replace("kept for an old client.", "")
    assert any("a colon, a reason" in e for e in map_errors(no_reason, SYNTHETIC))


def test_a_not_mapped_line_that_matches_nothing_is_stale() -> None:
    stale = CLEAN_MAP.replace("- `/legacy`:", "- `/legacy`, `/gone`:")
    assert any("/gone, which no longer exists" in e for e in map_errors(stale, SYNTHETIC))
    covered = CLEAN_MAP.replace("- `/legacy`:", "- `/legacy`, `/banks/{bank_id}/widgets`:")
    assert any("which an entry covers" in e for e in map_errors(covered, SYNTHETIC))


def test_a_new_page_group_must_be_claimed() -> None:
    sources = Sources(
        routes=SYNTHETIC.routes,
        pages={
            **SYNTHETIC.pages,
            "Bank dashboard": SYNTHETIC.pages["Bank dashboard"] | {"/insights"},
        },
        operator_groups=SYNTHETIC.operator_groups,
        test_ids=SYNTHETIC.test_ids,
        tracked=SYNTHETIC.tracked,
        read_text=SYNTHETIC.read_text,
    )
    assert any("/insights is neither covered" in e for e in map_errors(CLEAN_MAP, sources))


def test_every_entry_needs_a_depends_on_line() -> None:
    section = CLEAN_MAP.replace("## Not mapped", "### Orphan\n\n## Not mapped")
    assert any("no Depends on" in e for e in map_errors(section, SYNTHETIC))


def test_a_repeated_heading_is_reported() -> None:
    doubled = CLEAN_MAP.replace("### Health", "### Widgets")
    assert any("duplicate heading" in e for e in map_errors(doubled, SYNTHETIC))


def test_tokens_resolve_independently_of_parameter_names() -> None:
    assert route_exists("/banks/{x}/widgets/{y}", "tenant", SYNTHETIC.routes["tenant"])
    assert page_exists("/tenants/[orgId]", SYNTHETIC.pages["Staff console"])


def test_page_paths_drop_route_groups_and_read_metadata_files() -> None:
    assert page_path("app", "app/(app)/liquidity/nsfr/page.tsx") == "/liquidity/nsfr"
    assert page_path("app", "app/api/auth/[...nextauth]/route.ts") == "/api/auth/{}"
    assert page_path("app", "app/sitemap.ts") == "/sitemap.xml"
    assert page_path("app", "app/(app)/liquidity/Card.tsx") is None


def group_samples() -> Iterable[tuple[str, str]]:
    yield "/api/v1/banks/{}/icaap/cycles", "/banks/{}/icaap"
    yield "/api/v1/banks/{}", "/banks"
    yield "/api/v1/cases/{}", "/cases"
    yield "/api/health/live", "/api/health"


@pytest.mark.parametrize(("path", "group"), list(group_samples()))
def test_tenant_groups(path: str, group: str) -> None:
    assert tenant_group(normalize(path)) == group
