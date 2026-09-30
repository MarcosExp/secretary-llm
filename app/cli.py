"""Command-line interface.

    secretary task add "Submit assignment" -p P1 -a Studies -d 2027-01-15 -e 3
    secretary task list [--all | -s done] [-a Studies] [--due-before tomorrow]
    secretary task done 4
    secretary ask "add submit the lab report on Friday"
    secretary chat
    secretary pending | confirm 3 | reject 3
    secretary import notion [--dry-run]
    secretary db purge
    secretary wiki init
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta

from app import clock
from app.db import connect, db_path
from app.db.migrations import MigrationError, migrate
from app.db.retention import purge_archived
from app.db.seed import SeedError, seed_example
from app.importers.notion import NotionImportError
from modules.core import repo
from modules.core.repo import UNSET, NotFound, Task


def parse_date(value: str) -> date:
    """ISO date, or 'today' / 'tomorrow'. Anything fuzzier is the agent's job."""
    keywords = {"today": 0, "tomorrow": 1}
    if value.lower() in keywords:
        return clock.today() + timedelta(days=keywords[value.lower()])
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
    db.add_parser("purge", help="delete rows archived for more than 30 days (also runs daily in the app)")

    importer = groups.add_parser("import", help="import data from other tools").add_subparsers(
        dest="command", required=True)
    notion = importer.add_parser("notion", help="import from Notion (mapping in config/notion.yaml)")
    notion.add_argument("--dry-run", action="store_true", help="show what would be imported, change nothing")

    wiki = groups.add_parser("wiki", help="the user's Markdown wiki").add_subparsers(dest="command", required=True)
    wiki.add_parser("init", help="create the wiki (git repository and starter pages); never overwrites")

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

    ask = groups.add_parser("ask", help="send one request to the agent")
    ask.add_argument("text")
    ask.add_argument("-v", "--verbose", action="store_true", help="show the tool calls made")
    chat = groups.add_parser("chat", help="talk to the agent (empty line or Ctrl+D to exit)")
    chat.add_argument("-v", "--verbose", action="store_true", help="show the tool calls made")

    groups.add_parser("pending", help="list changes waiting for your confirmation")
    for name, help_text in (("confirm", "run a pending change"), ("reject", "discard a pending change")):
        cmd = groups.add_parser(name, help=help_text)
        cmd.add_argument("id", type=int)

    return parser


AGENT_COMMANDS = ("ask", "chat", "pending", "confirm", "reject")


def format_pending(actions, with_commands: bool = True) -> str:
    lines = []
    for action in actions:
        lines.append(f"Needs your confirmation (#{action.id}):")
        lines += [f"  {line}" for line in action.summary.splitlines()]
        if with_commands:
            lines.append(f"  -> secretary confirm {action.id}   |   secretary reject {action.id}")
    return "\n".join(lines)


def format_reply(reply, verbose: bool, commands: bool = True) -> str:
    lines = [reply.text]
    if reply.pending:
        lines.append(format_pending(reply.pending, commands))
    if verbose:
        for call in reply.tool_calls:
            status = "" if call.ok else "  [failed]"
            lines.append(f"  - {call.module}.{call.tool}({call.arguments}){status}")
    usage = reply.usage
    lines.append(
        f"[{len(reply.tool_calls)} tool calls, {usage.total_input_tokens} in / "
        f"{usage.output_tokens} out tokens, {', '.join(sorted(reply.models)) or '-'}]"
    )
    return "\n".join(lines)


def run_agent(args: argparse.Namespace) -> int:
    from app.agent import build_agent, open_agent_db
    from app.llm import LLMError

    conn = open_agent_db()
    try:
        agent = build_agent(conn)
        match args.group:
            case "ask":
                print(format_reply(agent.ask(args.text), args.verbose))
                return 0
            case "pending":
                print(format_pending(agent.pending_actions()) or "Nothing pending.")
                return 0
            case "confirm":
                print(agent.confirm(args.id))
                return 0
            case "reject":
                print(agent.reject(args.id))
                return 0
        history: list[dict] = []
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                break
            reply = agent.ask(text, history)
            history = reply.history
            print(format_reply(reply, args.verbose, commands=False))
            for action in reply.pending:
                answer = input(f"Confirm #{action.id}? [y/N] ").strip().lower()
                confirmed = answer in ("y", "yes", "s", "si", "sí")
                outcome = agent.confirm(action.id) if confirmed else agent.reject(action.id)
                print(outcome)
                # Tell the model what happened, so the next turn does not repeat the proposal.
                history.append({"role": "user", "content": f"<app_note>{outcome}</app_note>"})
        return 0
    except NotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def import_notion(conn: sqlite3.Connection, dry_run: bool) -> str:
    import os

    from app.importers import notion

    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise ValueError("set NOTION_TOKEN (a Notion internal integration token) to import")
    mappings = notion.load_mapping(notion.config_path())
    report = notion.run(conn, notion.NotionClient(token), mappings, dry_run=dry_run)
    header = "Dry run, nothing was saved:" if dry_run else "Imported from Notion:"
    return f"{header}\n{report.summary()}"


def init_wiki() -> str:
    import os
    from pathlib import Path

    from modules.wiki.store import WikiStore

    root = os.getenv("WIKI_DIR")
    if not root:
        raise ValueError("set WIKI_DIR to the wiki folder")
    created = WikiStore(Path(root)).init()
    return f"Wiki ready at {root}. " + (f"Created: {', '.join(created)}." if created else "Nothing to create.")


def run(args: argparse.Namespace, conn: sqlite3.Connection) -> str:
    match args.group, args.command:
        case "db", "migrate":
            return f"Database ready at {db_path()}."
        case "db", "seed-example":
            modules = seed_example(conn)
            return f"Example data loaded for: {', '.join(modules)}."
        case "db", "purge":
            deleted = purge_archived(conn)
            return "Deleted: " + ", ".join(f"{n} from {t}" for t, n in deleted.items()) if deleted else "Nothing to delete."
        case "import", "notion":
            return import_notion(conn, args.dry_run)
        case "wiki", "init":
            return init_wiki()
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
    if args.group in AGENT_COMMANDS:
        return run_agent(args)
    conn = connect()
    try:
        migrate(conn)
        print(run(args, conn))
        return 0
    except (NotFound, ValueError, MigrationError, SeedError, sqlite3.IntegrityError,
            NotionImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
