import json

import pytest

from app.agent.loop import LoopRunner
from app.agent.orchestrator import Agent
from app.db import connect
from app.db.migrations import migrate
from app.db.seed import seed_example
from app.llm.base import Usage
from app.llm.fake import ScriptedProvider, text, tool_call
from app.sdk.modules import load_modules
from modules.core import repo as core_repo
from tests.test_modules import make_notes_module


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_TIMEZONE", "UTC")
    connection = connect(tmp_path / "db.sqlite", cached_statements=0)
    migrate(connection)
    yield connection
    connection.close()


def agent(conn, steps, modules=None):
    provider = ScriptedProvider(steps)
    return Agent(LoopRunner(provider), modules or load_modules(), conn, "haiku"), provider


def log_rows(conn):
    return conn.execute("SELECT * FROM core_agent_log").fetchall()


def test_delegation_end_to_end(conn):
    bot, provider = agent(conn, [
        tool_call("delegate_to_core", {"task": "Add 'Buy printer ink' due 2027-01-15, P1"}),
        tool_call("add_task", {"title": "Buy printer ink", "due": "2027-01-15", "priority": "P1"}),
        text("Created task 1.", Usage(100, 10, cache_read_input_tokens=50)),
        text("Added 'Buy printer ink' for 15 January."),
    ])
    reply = bot.ask("add buy printer ink for the 15th, urgent")

    assert reply.text == "Added 'Buy printer ink' for 15 January."
    task = core_repo.get_task(conn, 1)
    assert (task.title, task.priority, task.due.isoformat()) == ("Buy printer ink", "P1", "2027-01-15")

    # The orchestrator only sees delegate tools; the subagent sees the module's own tools.
    orchestrator_req, sub_req = provider.requests[0], provider.requests[1]
    assert {t["name"] for t in orchestrator_req["tools"]} == {
        "delegate_to_calendar", "delegate_to_core", "delegate_to_jobs", "delegate_to_studies"}
    assert "add_task" in {t["name"] for t in sub_req["tools"]}
    assert "You manage the user's tasks" in sub_req["system"]
    assert "<context>Today is" in orchestrator_req["messages"][0]["content"]

    # Tool result reached the subagent as JSON.
    tool_result = provider.requests[2]["messages"][-1]["content"][0]
    assert json.loads(tool_result["content"])["title"] == "Buy printer ink"

    [row] = log_rows(conn)
    assert row["input"] == "add buy printer ink for the 15th, urgent"
    assert json.loads(row["tools_called"])[0]["tool"] == "add_task"
    assert row["input_tokens"] == 10 + 10 + 150 + 10  # four calls, cache reads included
    assert row["output_tokens"] == 5 + 5 + 10 + 5


def test_tool_errors_go_back_to_the_model(conn):
    bot, provider = agent(conn, [
        tool_call("delegate_to_core", {"task": "Add a task in area Gym"}),
        tool_call("add_task", {"title": "Leg day", "area": "Gym"}),
        text("There is no Gym area."),
        text("There is no 'Gym' area yet. Should I create it?"),
    ])
    reply = bot.ask("add leg day to gym")
    error = provider.requests[2]["messages"][-1]["content"][0]
    assert error["is_error"] is True
    assert "unknown area 'Gym'" in error["content"]
    assert reply.tool_calls[0].ok is False
    assert core_repo.list_tasks(conn) == []


def test_invalid_arguments_are_reported_not_raised(conn):
    bot, provider = agent(conn, [
        tool_call("delegate_to_core", {"task": "..."}),
        tool_call("add_task", {"title": "x", "priority": "urgent"}),
        text("Failed."),
        text("Failed."),
    ])
    bot.ask("...")
    error = provider.requests[2]["messages"][-1]["content"][0]
    assert error["is_error"] and "priority" in error["content"]


def test_cross_module_call_runs_in_one_transaction(conn):
    seed_example(conn)
    bot, _ = agent(conn, [
        tool_call("delegate_to_studies", {"task": "Add lab report for algebra due 2027-02-01"}),
        tool_call("add_course_task", {"course": "algebra", "title": "Lab report", "due": "2027-02-01"}),
        text("Created and linked."),
        text("Done."),
    ])
    reply = bot.ask("lab report for algebra due feb 1st")
    assert [(c.module, c.tool) for c in reply.tool_calls] == [
        ("studies", "add_course_task"), ("core", "add_task"),
    ]
    linked = conn.execute(
        """SELECT t.title FROM studies_course_tasks l
           JOIN core_tasks t ON t.id = l.task_id
           JOIN studies_courses c ON c.id = l.course_id
           WHERE c.name = 'Linear Algebra'"""
    ).fetchall()
    assert "Lab report" in [r["title"] for r in linked]


def test_denied_access_rolls_back_the_whole_tool_call(tmp_path, conn):
    make_notes_module(tmp_path)
    migrate(conn, tmp_path)
    conn.execute("INSERT INTO core_tasks (title) VALUES ('original')")
    conn.commit()
    bot, provider = agent(conn, [
        tool_call("delegate_to_notes", {"task": "tamper"}),
        tool_call("tamper", {}),
        text("Could not."),
        text("Could not."),
    ], modules=load_modules(tmp_path))
    bot.ask("tamper")

    error = provider.requests[2]["messages"][-1]["content"][0]
    assert error["is_error"] and "may not update core_tasks" in error["content"]
    assert conn.execute("SELECT title FROM core_tasks").fetchone()[0] == "original"
    assert conn.execute("SELECT COUNT(*) FROM notes_items").fetchone()[0] == 0  # first write undone


def test_new_module_folder_works_end_to_end(tmp_path, conn):
    make_notes_module(tmp_path)
    migrate(conn, tmp_path)
    bot, _ = agent(conn, [
        tool_call("delegate_to_notes", {"task": "Save note: call the bank"}),
        tool_call("add_note", {"text": "call the bank"}),
        text("Saved note 1."),
        text("Saved."),
    ], modules=load_modules(tmp_path))
    bot.ask("note: call the bank")
    assert conn.execute("SELECT text FROM notes_items").fetchone()[0] == "call the bank"


def test_history_carries_over_between_turns(conn):
    bot, provider = agent(conn, [text("Hi!"), text("You said hello.")])
    first = bot.ask("hello")
    bot.ask("what did I say?", first.history)
    messages = provider.requests[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_date_context_lists_the_next_two_weeks(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import clock
    from app.agent.orchestrator import _date_context

    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("UTC")))
    context = _date_context()
    assert "Today is Tuesday 2026-09-29" in context
    assert "Fri 2026-10-02" in context and "Tue 2026-10-13" in context


def test_refusal_and_runaway_loops(conn):
    from app.llm.base import LLMResponse

    bot, _ = agent(conn, [LLMResponse([], "refusal", "fake")])
    assert bot.ask("...").text == "I can't help with that request."

    def keep_delegating(request):
        is_orchestrator = any(t["name"].startswith("delegate_to_") for t in request["tools"])
        return tool_call("delegate_to_core", {"task": "x"}) if is_orchestrator else text("ok")

    bot, _ = agent(conn, [keep_delegating] * 30)
    reply = bot.ask("loop forever")
    assert "stopped after" in reply.text
