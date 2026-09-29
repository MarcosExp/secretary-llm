"""Data access for job applications. Callers own the transaction; nothing is deleted."""

from dataclasses import dataclass
from datetime import date

from app.sdk.errors import NotFound

STAGES = ("researching", "to_apply", "applied", "assessment", "interview", "final",
          "offer", "rejected", "no_response", "archived")
CLOSED_STAGES = ("rejected", "no_response", "archived")
FIELDS = ("company", "role", "program", "stage", "priority", "location", "visa", "referral",
          "process_open", "link", "contact", "applied_on", "deadline", "follow_up_by",
          "next_step", "notes")


@dataclass(frozen=True)
class Application:
    id: int
    company: str
    role: str | None
    program: str | None
    stage: str
    priority: str | None
    location: str | None
    visa: str | None
    referral: bool
    process_open: bool | None
    link: str | None
    contact: str | None
    applied_on: str | None
    deadline: str | None
    follow_up_by: str | None
    next_step: str | None
    notes: str | None

    @classmethod
    def from_row(cls, row) -> "Application":
        values = {name: row[name] for name in ("id", *FIELDS)}
        values["referral"] = bool(values["referral"])
        values["process_open"] = None if values["process_open"] is None else bool(values["process_open"])
        return cls(**values)


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


def application_id(db, company: str) -> int:
    """By exact company name (any case), else a unique partial match."""
    rows = db.execute(
        "SELECT id, company, role FROM jobs_applications WHERE stage != 'archived' ORDER BY company"
    ).fetchall()
    wanted = company.strip().lower()
    matches = [r for r in rows if r["company"].lower() == wanted] or \
              [r for r in rows if wanted in r["company"].lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        raise NotFound(f"no application matches '{company}'")
    listing = ", ".join(f"#{r['id']} {r['company']} ({r['role'] or '-'})" for r in matches)
    raise ValueError(f"'{company}' matches several applications: {listing}; use the id")


def get(db, app_id: int) -> Application:
    row = db.execute("SELECT * FROM jobs_applications WHERE id = ?", (app_id,)).fetchone()
    if row is None:
        raise NotFound(f"application #{app_id} not found")
    return Application.from_row(row)


def add(db, company: str, **fields) -> Application:
    if not company.strip():
        raise ValueError("company cannot be empty")
    values = {"company": company.strip(), **{k: _iso(v) for k, v in fields.items() if v is not None}}
    columns = ", ".join(values)
    app_id = db.execute(
        f"INSERT INTO jobs_applications ({columns}) VALUES ({', '.join('?' * len(values))})",
        tuple(values.values()),
    ).lastrowid
    return get(db, app_id)


def update(db, app_id: int, **fields) -> Application:
    get(db, app_id)
    changes = {k: _iso(v) for k, v in fields.items() if k in FIELDS}
    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.execute(f"UPDATE jobs_applications SET {assignments} WHERE id = ?", (*changes.values(), app_id))
    return get(db, app_id)


def list_applications(db, stages: tuple[str, ...] | None = None) -> list[Application]:
    """Open applications by default, soonest deadline or follow-up first."""
    if stages:
        where, params = f"stage IN ({', '.join('?' * len(stages))})", stages
    else:
        where, params = f"stage NOT IN ({', '.join('?' * len(CLOSED_STAGES))})", CLOSED_STAGES
    rows = db.execute(
        f"""SELECT * FROM jobs_applications WHERE {where}
            ORDER BY COALESCE(MIN(deadline, follow_up_by), deadline, follow_up_by) IS NULL,
                     COALESCE(MIN(deadline, follow_up_by), deadline, follow_up_by), company""",
        params,
    )
    return [Application.from_row(row) for row in rows]


def link_task(db, app_id: int, task_id: int) -> None:
    db.execute(
        "INSERT OR IGNORE INTO jobs_application_tasks (application_id, task_id) VALUES (?, ?)",
        (app_id, task_id),
    )


def open_tasks(db, app_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT t.id, t.title, t.status, t.priority, t.due
           FROM jobs_application_tasks l JOIN core_tasks t ON t.id = l.task_id
           WHERE l.application_id = ? AND t.status IN ('todo', 'doing')
           ORDER BY t.due IS NULL, t.due""",
        (app_id,),
    )
    return [dict(row) for row in rows]
