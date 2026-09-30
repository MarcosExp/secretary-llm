"""One-way import from Notion databases into Secretary.

    secretary import notion [--dry-run]

Reads the mapping from <data dir>/config/notion.yaml (see examples/notion.example.yaml):
which Notion database feeds each kind of record, which property holds each field, and
how select values translate. A value mapped as "todo | Blocked" becomes "todo" and
adds "Blocked" to the record's notes, so nothing the mapping can't express is lost.

Every imported row keeps "notion:<page id>" in external_ref. Running the import again
only adds pages it has not seen and never overwrites rows, so Notion and Secretary
can be used side by side during the transition. Rows deleted after 30 days archived
are remembered in core_purged and not imported again.

The importer is trusted system code: it writes directly to several modules' tables
in one transaction (all or nothing).
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

from app.config import config_path as main_config_path

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
KINDS = ("courses", "study_units", "applications", "tasks")  # import order: parents first


class NotionImportError(Exception):
    pass


@dataclass
class Report:
    created: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{kind}: {self.created[kind]} new, {self.skipped[kind]} already imported"
                 for kind in KINDS if kind in self.created or kind in self.skipped]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines) or "Nothing configured to import."


# --- Notion API ---

class NotionClient:
    def __init__(self, token: str, session=None):
        if session is None:
            import requests

            session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION})
        self.session = session

    def pages(self, database_id: str) -> Iterator[dict]:
        cursor = None
        while True:
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            response = self.session.post(f"{API}/databases/{database_id}/query", json=body, timeout=30)
            if response.status_code in (401, 403, 404):
                raise NotionImportError(
                    f"Notion refused database {database_id} ({response.status_code}); check NOTION_TOKEN "
                    "and that the database is shared with the integration"
                )
            response.raise_for_status()
            data = response.json()
            yield from (p for p in data["results"] if not p.get("archived") and not p.get("in_trash"))
            if not data.get("has_more"):
                return
            cursor = data["next_cursor"]


def plain(prop: dict | None):
    """Python value of a Notion property."""
    if not prop:
        return None
    kind = prop["type"]
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(part["plain_text"] for part in value).strip() or None
    if kind in ("select", "status"):
        return value["name"] if value else None
    if kind == "multi_select":
        return [option["name"] for option in value]
    if kind == "date":
        return value["start"][:10] if value else None
    if kind == "relation":
        return [page_id(item["id"]) for item in value]
    if kind in ("number", "url", "checkbox", "email", "phone_number"):
        return value
    return None


def page_id(value: str) -> str:
    return value.replace("-", "").lower()


# --- Mapping ---

@dataclass
class Mapping:
    kind: str
    database_id: str
    properties: dict[str, str]
    values: dict[str, dict]
    report: Report = field(default_factory=Report)  # replaced by run() for each import

    def get(self, page: dict, name: str):
        prop_name = self.properties.get(name)
        return plain(page["properties"].get(prop_name)) if prop_name else None

    def translate(self, page: dict, name: str, default=None) -> tuple[object, str | None]:
        """Mapped value of a select property, plus an optional note to keep."""
        raw = self.get(page, name)
        if raw is None:
            return default, None
        table = self.values.get(name, {})
        if raw not in table:
            self.report.warnings.append(
                f"{self.kind}: no mapping for {name} '{raw}', used {default!r} (add it to notion.yaml)"
            )
            return default, f"{name}: {raw}"
        target = table[raw]
        if isinstance(target, str) and "|" in target:
            value, note = (part.strip() for part in target.split("|", 1))
            return value, note
        return target, None


def load_mapping(path: Path) -> dict[str, Mapping]:
    if not path.exists():
        raise NotionImportError(f"missing {path}; copy examples/notion.example.yaml there and fill it in")
    config = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("notion", {})
    return {
        kind: Mapping(kind, section["database_id"], section.get("properties", {}),
                      section.get("values", {}))
        for kind, section in config.items()
        if kind in KINDS and section and section.get("database_id")
    }


def config_path() -> Path:
    """notion.yaml, next to the main config.yaml."""
    main = main_config_path() or Path("/config/config.yaml")
    return main.parent / "notion.yaml"


# --- Import ---

class _DryRun(Exception):
    pass


def run(conn, client: NotionClient, mappings: dict[str, Mapping], dry_run: bool = False) -> Report:
    report = Report()
    for mapping in mappings.values():
        mapping.report = report
    try:
        with conn:
            for kind in KINDS:
                if kind in mappings:
                    importer = IMPORTERS[kind]
                    for page in client.pages(mappings[kind].database_id):
                        ref = f"notion:{page_id(page['id'])}"
                        if _existing(conn, kind, ref) is not None or _purged(conn, ref):
                            report.skipped[kind] += 1
                            continue
                        if importer(conn, mappings[kind], page, ref):
                            report.created[kind] += 1
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass  # the transaction was rolled back
    return report


TABLES = {"courses": "studies_courses", "study_units": "studies_units",
          "applications": "jobs_applications", "tasks": "core_tasks"}


def _existing(conn, kind: str, ref: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {TABLES[kind]} WHERE external_ref = ?", (ref,)).fetchone()
    return row[0] if row else None


def _purged(conn, ref: str) -> bool:
    """Imported once, archived and deleted after 30 days: not imported again."""
    return conn.execute("SELECT 1 FROM core_purged WHERE external_ref = ?", (ref,)).fetchone() is not None


def _ref_id(conn, kind: str, notion_ids: list[str] | None) -> int | None:
    for notion_id in notion_ids or []:
        found = _existing(conn, kind, f"notion:{notion_id}")
        if found is not None:
            return found
    return None


def _flag(value) -> int | None:
    """1/0 from a mapped yes/no value ("false" from a "false | note" mapping is still false)."""
    if value is None:
        return None
    if isinstance(value, str):
        return 0 if value.strip().lower() in ("false", "no", "0", "") else 1
    return int(bool(value))


def _notes(*parts) -> str | None:
    text = "\n".join(p for p in parts if p)
    return text or None


def _insert(conn, table: str, values: dict) -> int:
    values = {k: v for k, v in values.items() if v is not None}
    return conn.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
        tuple(values.values()),
    ).lastrowid


def _named(conn, table: str, name: str | None) -> int | None:
    """Id of a row by name in core_areas / studies_programs, creating it if needed."""
    if not name:
        return None
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    return row[0] if row else conn.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,)).lastrowid


# Each importer inserts one page and returns whether it did.

def import_course(conn, m: Mapping, page: dict, ref: str) -> bool:
    status, note = m.translate(page, "status", "enrolled")
    _insert(conn, "studies_courses", {
        "name": m.get(page, "title") or "(untitled)",
        "program_id": _named(conn, "studies_programs", m.get(page, "program")),
        "status": status,
        "exam_date": m.get(page, "exam_date"),
        "ects": m.get(page, "ects"),
        "term": m.get(page, "term"),
        "grade": m.get(page, "grade"),
        "notes": _notes(m.get(page, "notes"), note),
        "external_ref": ref,
    })
    return True


def import_unit(conn, m: Mapping, page: dict, ref: str) -> bool:
    course = _ref_id(conn, "courses", m.get(page, "course"))
    if course is None:
        m.report.warnings.append(f"study_units: '{m.get(page, 'title')}' has no imported course, skipped")
        return False
    status, status_note = m.translate(page, "status", "pending")
    confidence, _ = m.translate(page, "confidence", None)
    position = m.get(page, "position")
    _insert(conn, "studies_units", {
        "course_id": course,
        "unit": m.get(page, "title") or "(untitled)",
        "start": m.get(page, "start"),
        '"end"': m.get(page, "end"),  # quoted: END is an SQL keyword
        "estimate_h": m.get(page, "estimate_h") or None,
        "confidence": confidence,
        "status": status,
        "position": int(position) if position is not None else None,
        "weight": m.get(page, "weight"),
        "notes": _notes(m.get(page, "notes"), status_note),
        "external_ref": ref,
    })
    return True


def import_application(conn, m: Mapping, page: dict, ref: str) -> bool:
    stage, stage_note = m.translate(page, "stage", "researching")
    priority, _ = m.translate(page, "priority", None)
    visa, _ = m.translate(page, "visa", None)
    process_open, open_note = m.translate(page, "process_open", None)
    _insert(conn, "jobs_applications", {
        "company": m.get(page, "title") or "(untitled)",
        "role": m.get(page, "role"),
        "program": m.get(page, "program"),
        "stage": stage,
        "priority": priority,
        "location": m.get(page, "location"),
        "visa": visa,
        "referral": 1 if m.get(page, "referral") else 0,
        "process_open": _flag(process_open),
        "link": m.get(page, "link"),
        "contact": m.get(page, "contact"),
        "applied_on": m.get(page, "applied_on"),
        "deadline": m.get(page, "deadline"),
        "follow_up_by": m.get(page, "follow_up_by"),
        "next_step": m.get(page, "next_step"),
        "notes": _notes(m.get(page, "notes"), stage_note, open_note),
        "external_ref": ref,
    })
    return True


def import_task(conn, m: Mapping, page: dict, ref: str) -> bool:
    status, status_note = m.translate(page, "status", "todo")
    priority, _ = m.translate(page, "priority", "P2")
    # Areas are free names: used as they are unless notion.yaml renames them.
    if "area" in m.values:
        area, area_note = m.translate(page, "area", None)
    else:
        area, area_note = m.get(page, "area"), None
    estimate = m.get(page, "estimate_h")
    task_id = _insert(conn, "core_tasks", {
        "title": m.get(page, "title") or "(untitled)",
        "status": status,
        "priority": priority,
        "area_id": _named(conn, "core_areas", area),
        "due": m.get(page, "due"),
        "estimate_h": estimate if estimate and estimate > 0 else None,
        "notes": _notes(m.get(page, "notes"), status_note, area_note),
        "external_ref": ref,
    })
    if status == "done":
        conn.execute("UPDATE core_tasks SET completed_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
    if (course := _ref_id(conn, "courses", m.get(page, "course"))) is not None:
        conn.execute("INSERT OR IGNORE INTO studies_course_tasks (course_id, task_id) VALUES (?, ?)",
                     (course, task_id))
    if (application := _ref_id(conn, "applications", m.get(page, "application"))) is not None:
        conn.execute("INSERT OR IGNORE INTO jobs_application_tasks (application_id, task_id) VALUES (?, ?)",
                     (application, task_id))
    return True


IMPORTERS = {"courses": import_course, "study_units": import_unit,
             "applications": import_application, "tasks": import_task}
