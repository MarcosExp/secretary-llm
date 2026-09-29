"""Data access for core areas and tasks.

Functions do not commit: the caller owns the transaction (`with conn:`), so one
agent tool call or CLI command is one transaction. There is no delete; tasks
are completed or archived.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

PRIORITIES = ("P1", "P2", "P3")
OPEN_STATUSES = ("todo", "doing")
STATUSES = (*OPEN_STATUSES, "done", "archived")
UNSET = object()  # distinguishes "not given" from "clear this field" (None) in update_task


class NotFound(LookupError):
    pass


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    status: str
    priority: str
    area: str | None
    due: date | None
    estimate_h: float | None
    notes: str | None
    created_at: str
    updated_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            area=row["area"],
            due=date.fromisoformat(row["due"]) if row["due"] else None,
            estimate_h=row["estimate_h"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )


# --- Areas ---

def add_area(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("area name cannot be empty")
    return conn.execute("INSERT INTO core_areas (name) VALUES (?)", (name,)).lastrowid


def list_areas(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM core_areas WHERE status = 'active' ORDER BY name")
    return [row["name"] for row in rows]


def area_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM core_areas WHERE name = ? AND status = 'active'", (name.strip(),)
    ).fetchone()
    if row is None:
        available = ", ".join(list_areas(conn)) or "none yet"
        raise NotFound(f"unknown area '{name}' (available: {available})")
    return row["id"]


# --- Tasks ---

_SELECT_TASK = """
    SELECT t.*, a.name AS area
    FROM core_tasks t LEFT JOIN core_areas a ON a.id = t.area_id
"""


def _check_priority(priority: str) -> None:
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")


def _check_estimate(estimate_h: float | None) -> None:
    if estimate_h is not None and estimate_h <= 0:
        raise ValueError("estimate must be a positive number of hours")


def add_task(
    conn: sqlite3.Connection,
    title: str,
    *,
    priority: str = "P2",
    area: str | None = None,
    due: date | None = None,
    estimate_h: float | None = None,
    notes: str | None = None,
) -> Task:
    title = title.strip()
    if not title:
        raise ValueError("title cannot be empty")
    _check_priority(priority)
    _check_estimate(estimate_h)
    task_id = conn.execute(
        """INSERT INTO core_tasks (title, priority, area_id, due, estimate_h, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            title,
            priority,
            area_id(conn, area) if area else None,
            due.isoformat() if due else None,
            estimate_h,
            notes,
        ),
    ).lastrowid
    return get_task(conn, task_id)


def get_task(conn: sqlite3.Connection, task_id: int) -> Task:
    row = conn.execute(f"{_SELECT_TASK} WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        raise NotFound(f"task {task_id} not found")
    return Task.from_row(row)


def list_tasks(
    conn: sqlite3.Connection,
    *,
    statuses: tuple[str, ...] = OPEN_STATUSES,
    area: str | None = None,
    due_before: date | None = None,
) -> list[Task]:
    """Tasks ordered by priority, then due date (undated last), then creation."""
    unknown = set(statuses) - set(STATUSES)
    if unknown:
        raise ValueError(f"unknown status: {', '.join(sorted(unknown))}")
    clauses = [f"t.status IN ({', '.join('?' * len(statuses))})"]
    params: list = list(statuses)
    if area:
        clauses.append("t.area_id = ?")
        params.append(area_id(conn, area))
    if due_before:
        clauses.append("t.due <= ?")
        params.append(due_before.isoformat())
    rows = conn.execute(
        f"{_SELECT_TASK} WHERE {' AND '.join(clauses)} "
        "ORDER BY t.priority, t.due IS NULL, t.due, t.id",
        params,
    )
    return [Task.from_row(row) for row in rows]


def update_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    title=UNSET,
    priority=UNSET,
    area=UNSET,
    due=UNSET,
    estimate_h=UNSET,
    notes=UNSET,
    status=UNSET,
) -> Task:
    """Change the given fields. Passing None clears an optional field.

    Status can only move between open states here; use complete_task or archive_task.
    """
    current = get_task(conn, task_id)
    changes: dict = {}
    if title is not UNSET:
        if not title or not title.strip():
            raise ValueError("title cannot be empty")
        changes["title"] = title.strip()
    if priority is not UNSET:
        _check_priority(priority)
        changes["priority"] = priority
    if area is not UNSET:
        changes["area_id"] = area_id(conn, area) if area else None
    if due is not UNSET:
        changes["due"] = due.isoformat() if due else None
    if estimate_h is not UNSET:
        _check_estimate(estimate_h)
        changes["estimate_h"] = estimate_h
    if notes is not UNSET:
        changes["notes"] = notes
    if status is not UNSET:
        if status not in OPEN_STATUSES:
            raise ValueError("use complete_task or archive_task to close a task")
        changes["status"] = status
        if current.status == "done":
            changes["completed_at"] = None  # reopening
    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        conn.execute(
            f"UPDATE core_tasks SET {assignments} WHERE id = ?", (*changes.values(), task_id)
        )
    return get_task(conn, task_id)


def complete_task(conn: sqlite3.Connection, task_id: int) -> Task:
    task = get_task(conn, task_id)
    if task.status == "archived":
        raise ValueError(f"task {task_id} is archived")
    conn.execute(
        "UPDATE core_tasks SET status = 'done', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (task_id,),
    )
    return get_task(conn, task_id)


def archive_task(conn: sqlite3.Connection, task_id: int) -> Task:
    get_task(conn, task_id)
    conn.execute("UPDATE core_tasks SET status = 'archived' WHERE id = ?", (task_id,))
    return get_task(conn, task_id)
