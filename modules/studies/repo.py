"""Data access for programs, courses and study units. Callers own the transaction."""

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.sdk.errors import NotFound


@dataclass(frozen=True)
class Unit:
    id: int
    unit: str
    status: str
    start: date | None
    end: date | None
    estimate_h: float | None
    done_h: float
    confidence: int | None


@dataclass(frozen=True)
class CourseProgress:
    id: int
    name: str
    program: str | None
    status: str
    exam_date: date | None
    days_to_exam: int | None
    estimate_h: float
    done_h: float
    units: list[Unit]


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# --- Lookups ---

def course_id(db, name: str) -> int:
    """Exact name (any case), else a unique partial match ("algebra" -> "Linear Algebra")."""
    rows = db.execute(
        "SELECT id, name FROM studies_courses WHERE status != 'archived' ORDER BY name"
    ).fetchall()
    wanted = name.strip().lower()
    exact = [r for r in rows if r["name"].lower() == wanted]
    partial = [r for r in rows if wanted in r["name"].lower()]
    matches = exact or partial
    if len(matches) == 1:
        return matches[0]["id"]
    if not matches:
        available = ", ".join(r["name"] for r in rows) or "none yet"
        raise NotFound(f"no course matches '{name}' (courses: {available})")
    raise ValueError(f"'{name}' matches several courses: {', '.join(r['name'] for r in matches)}")


def unit_id(db, course: int, name: str | None) -> int:
    """The named unit, or the course's single in-progress unit when no name is given."""
    rows = db.execute(
        "SELECT id, unit, status FROM studies_units WHERE course_id = ? AND status != 'archived' "
        "ORDER BY start IS NULL, start, id",
        (course,),
    ).fetchall()
    if name:
        matches = [r for r in rows if name.strip().lower() in r["unit"].lower()]
    else:
        matches = [r for r in rows if r["status"] == "in_progress"]
    if len(matches) == 1:
        return matches[0]["id"]
    listing = ", ".join(f"{r['unit']} ({r['status']})" for r in rows) or "none yet"
    if not matches:
        what = f"no unit matches '{name}'" if name else "no unit is in progress"
        raise NotFound(f"{what} (units: {listing})")
    raise ValueError(f"several units match; name one of: {listing}")


# --- Programs and courses ---

def add_program(db, name: str) -> int:
    return db.execute("INSERT INTO studies_programs (name) VALUES (?)", (name.strip(),)).lastrowid


def add_course(db, name: str, program: str | None = None, exam_date: date | None = None) -> int:
    program_id = None
    if program:
        row = db.execute(
            "SELECT id FROM studies_programs WHERE name = ?", (program.strip(),)
        ).fetchone()
        program_id = row["id"] if row else add_program(db, program)
    return db.execute(
        "INSERT INTO studies_courses (name, program_id, exam_date) VALUES (?, ?, ?)",
        (name.strip(), program_id, exam_date.isoformat() if exam_date else None),
    ).lastrowid


def set_exam_date(db, course: int, exam_date: date | None) -> None:
    db.execute(
        "UPDATE studies_courses SET exam_date = ? WHERE id = ?",
        (exam_date.isoformat() if exam_date else None, course),
    )


def set_course_status(db, course: int, status: str) -> None:
    db.execute("UPDATE studies_courses SET status = ? WHERE id = ?", (status, course))


def list_courses(db, include_finished: bool = False) -> list[dict]:
    statuses = ("enrolled", "passed") if include_finished else ("enrolled",)
    rows = db.execute(
        f"""SELECT c.id, c.name, c.status, c.exam_date, p.name AS program
            FROM studies_courses c LEFT JOIN studies_programs p ON p.id = c.program_id
            WHERE c.status IN ({', '.join('?' * len(statuses))})
            ORDER BY c.exam_date IS NULL, c.exam_date, c.name""",
        statuses,
    )
    return [dict(row) for row in rows]


# --- Units ---

def add_unit(db, course: int, unit: str, estimate_h: float | None = None,
             start: date | None = None, end: date | None = None) -> int:
    return db.execute(
        """INSERT INTO studies_units (course_id, unit, estimate_h, start, "end")
           VALUES (?, ?, ?, ?, ?)""",
        (course, unit.strip(), estimate_h,
         start.isoformat() if start else None, end.isoformat() if end else None),
    ).lastrowid


def log_hours(db, unit: int, hours: float, confidence: int | None = None) -> None:
    """Add study hours to a unit. A pending unit becomes in progress."""
    if hours <= 0:
        raise ValueError("hours must be positive")
    db.execute(
        """UPDATE studies_units
           SET done_h = done_h + ?,
               confidence = COALESCE(?, confidence),
               status = CASE WHEN status = 'pending' THEN 'in_progress' ELSE status END
           WHERE id = ?""",
        (hours, confidence, unit),
    )


def set_unit_status(db, unit: int, status: str) -> None:
    db.execute("UPDATE studies_units SET status = ? WHERE id = ?", (status, unit))


def progress(db, today: date, course: int | None = None) -> list[CourseProgress]:
    where, params = ("AND c.id = ?", (course,)) if course else ("", ())
    courses = db.execute(
        f"""SELECT c.id, c.name, c.status, c.exam_date, p.name AS program
            FROM studies_courses c LEFT JOIN studies_programs p ON p.id = c.program_id
            WHERE c.status = 'enrolled' {where}
            ORDER BY c.exam_date IS NULL, c.exam_date, c.name""",
        params,
    ).fetchall()
    result = []
    for c in courses:
        units = [
            Unit(r["id"], r["unit"], r["status"], _date(r["start"]), _date(r["end"]),
                 r["estimate_h"], r["done_h"], r["confidence"])
            for r in db.execute(
                """SELECT * FROM studies_units WHERE course_id = ? AND status != 'archived'
                   ORDER BY start IS NULL, start, id""",
                (c["id"],),
            )
        ]
        exam = _date(c["exam_date"])
        result.append(CourseProgress(
            id=c["id"], name=c["name"], program=c["program"], status=c["status"],
            exam_date=exam, days_to_exam=(exam - today).days if exam else None,
            estimate_h=sum(u.estimate_h or 0 for u in units),
            done_h=sum(u.done_h for u in units),
            units=units,
        ))
    return result


# --- Links to core tasks ---

def link_task(db, course: int, task_id: int) -> None:
    db.execute(
        "INSERT OR IGNORE INTO studies_course_tasks (course_id, task_id) VALUES (?, ?)",
        (course, task_id),
    )


def course_tasks(db, course: int, include_closed: bool = False) -> list[dict]:
    closed = "" if include_closed else "AND t.status IN ('todo', 'doing')"
    rows = db.execute(
        f"""SELECT t.id, t.title, t.status, t.priority, t.due
            FROM studies_course_tasks l JOIN core_tasks t ON t.id = l.task_id
            WHERE l.course_id = ? {closed}
            ORDER BY t.due IS NULL, t.due, t.id""",
        (course,),
    )
    return [dict(row) for row in rows]
