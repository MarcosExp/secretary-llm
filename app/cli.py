"""Command-line interface.

    secretary task add "Submit assignment" -p P1 -a Studies -d 2027-01-15 -e 3
    secretary task list [--all | -s done] [-a Studies] [--due-before tomorrow]
    secretary task done 4
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta

from app.db import connect, db_path
from app.db.migrations import MigrationError, migrate
from app.db.seed import SeedError, seed_example
from modules.core import repo
from modules.core.repo import UNSET, NotFound, Task


def parse_date(value: str) -> date:
    """ISO date, or 'today' / 'tomorrow'. Anything fuzzier is the agent's job."""
    keywords = {"today": 0, "tomorrow": 1}
    if value.lower() in keywords:
        return date.today() + timedelta(days=keywords[value.lower()])
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date '{value}' (use YYYY-MM-DD, today or tomorrow)")


def positive_hours(value: str) -> float:
    try:
        hours = float(value)
    except ValueError:
        hours = 0
    if hours <= 0:
        raise argparse.ArgumentTypeError(f"invalid estimate '{value}' (positive number of hours)")
    return hours


def format_tasks(tasks: list[Task]) -> str:
    if not tasks:
        return "No tasks."
    rows = [("ID", "PRI", "STATUS", "DUE", "AREA", "EST", "TITLE")]
    for t in tasks:
        rows.append((
            str(t.id),
            t.priority,
            t.status,
            t.due.isoformat() if t.due else "-",
            t.area or "-",
            f"{t.estimate_h:g}h" if t.estimate_h else "-",
            t.title,
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)) + "  " + row[-1]
        for row in rows
    )


def format_task(t: Task) -> str:
    fields = [
        ("id", t.id),
        ("title", t.title),
        ("status", t.status),
        ("priority", t.priority),
        ("area", t.area or "-"),
        ("due", t.due.isoformat() if t.due else "-"),
        ("estimate", f"{t.estimate_h:g}h" if t.estimate_h else "-"),
        ("notes", t.notes or "-"),
        ("created", t.created_at),
        ("updated", t.updated_at),
        ("completed", t.completed_at or "-"),
    ]
    return "\n".join(f"{name:<10}{value}" for name, value in fields)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secretary", description="Personal organization agent.")
    groups = parser.add_subparsers(dest="group", required=True)

    db = groups.add_parser("db", help="database maintenance").add_subparsers(dest="command", required=True)
    db.add_parser("migrate", help="apply pending migrations")
    db.add_parser("seed-example", help="load fictional example data into an empty database")

    area = groups.add_parser("area", help="manage areas").add_subparsers(dest="command", required=True)
    area.add_parser("list", help="list active areas")
    area_add = area.add_parser("add", help="add an area")
    area_add.add_argument("name")

    task = groups.add_parser("task", help="manage tasks").add_subparsers(dest="command", required=True)

    add = task.add_parser("add", help="add a task")
    add.add_argument("title")
    add.add_argument("-p", "--priority", choices=repo.PRIORITIES, default="P2")
    add.add_argument("-a", "--area")
    add.add_argument("-d", "--due", type=parse_date)
    add.add_argument("-e", "--estimate", type=positive_hours, help="hours")
    add.add_argument("-n", "--notes")

    ls = task.add_parser("list", help="list tasks (open ones by default)")
    which = ls.add_mutually_exclusive_group()
    which.add_argument("-s", "--status", action="append", choices=repo.STATUSES)
    which.add_argument("--all", action="store_true", help="include done and archived")
    ls.add_argument("-a", "--area")
    ls.add_argument("--due-before", type=parse_date)

    show = task.add_parser("show", help="show one task")
    show.add_argument("id", type=int)

    update = task.add_parser("update", help="change a task")
    update.add_argument("id", type=int)
    update.add_argument("--title")
    update.add_argument("-p", "--priority", choices=repo.PRIORITIES)
    update.add_argument("-a", "--area")
    update.add_argument("-d", "--due", type=parse_date)
    update.add_argument("-e", "--estimate", type=positive_hours)
    update.add_argument("-n", "--notes")
    update.add_argument("-s", "--status", choices=repo.OPEN_STATUSES)
    for field in ("area", "due", "estimate", "notes"):
        update.add_argument(f"--clear-{field}", action="store_true", help=f"remove the {field}")

    for name, help_text in (("done", "mark a task as done"), ("archive", "archive a task")):
        cmd = task.add_parser(name, help=help_text)
        cmd.add_argument("id", type=int)

    return parser


def run(args: argparse.Namespace, conn: sqlite3.Connection) -> str:
    match args.group, args.command:
        case "db", "migrate":
            return f"Database ready at {db_path()}."
        case "db", "seed-example":
            modules = seed_example(conn)
            return f"Example data loaded for: {', '.join(modules)}."
        case "area", "list":
            return "\n".join(repo.list_areas(conn)) or "No areas."
        case "area", "add":
            with conn:
                repo.add_area(conn, args.name)
            return f"Added area {args.name.strip()}."
        case "task", "add":
            with conn:
                task = repo.add_task(
                    conn, args.title, priority=args.priority, area=args.area,
                    due=args.due, estimate_h=args.estimate, notes=args.notes,
                )
            return f"Added task {task.id}: {task.title}"
        case "task", "list":
            statuses = repo.STATUSES if args.all else tuple(args.status or repo.OPEN_STATUSES)
            return format_tasks(
                repo.list_tasks(conn, statuses=statuses, area=args.area, due_before=args.due_before)
            )
        case "task", "show":
            return format_task(repo.get_task(conn, args.id))
        case "task", "update":
            def pick(value, clear):
                return None if clear else (UNSET if value is None else value)

            with conn:
                task = repo.update_task(
                    conn, args.id,
                    title=UNSET if args.title is None else args.title,
                    priority=UNSET if args.priority is None else args.priority,
                    area=pick(args.area, args.clear_area),
                    due=pick(args.due, args.clear_due),
                    estimate_h=pick(args.estimate, args.clear_estimate),
                    notes=pick(args.notes, args.clear_notes),
                    status=UNSET if args.status is None else args.status,
                )
            return format_task(task)
        case "task", "done":
            with conn:
                task = repo.complete_task(conn, args.id)
            return f"Done: {task.title}"
        case "task", "archive":
            with conn:
                task = repo.archive_task(conn, args.id)
            return f"Archived: {task.title}"
    raise AssertionError(f"unhandled command {args.group} {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect()
    try:
        migrate(conn)
        print(run(args, conn))
        return 0
    except (NotFound, ValueError, MigrationError, SeedError, sqlite3.IntegrityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
