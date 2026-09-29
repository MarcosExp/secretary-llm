import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent.loop import LoopRunner
from app.agent.orchestrator import Agent
from app.db import connect
from app.db.migrations import migrate
from app.llm.fake import ScriptedProvider, text, tool_call
from app.sdk import NotFound, ToolError
from app.sdk.modules import load_modules
from modules.calendar.client import MARKER, Event, FakeCalendar, GoogleCalendar

TZ = ZoneInfo("Europe/Berlin")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def calendar():
    return FakeCalendar([
        Event("study", "Study block", at(1, 17), at(1, 18), created_by_secretary=True),
        Event("meeting", "Team meeting", at(1, 10), at(1, 11), has_other_attendees=True),
    ])


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_TIMEZONE", "Europe/Berlin")
    connection = connect(tmp_path / "db.sqlite", cached_statements=0)
    migrate(connection)
    yield connection
    connection.close()


def agent_with(conn, calendar, *steps):
    """Orchestrator delegates to core, core runs the given tool steps, both then answer."""
    script = [tool_call("delegate_to_calendar", {"task": "calendar task"}), *steps,
              text("module report"), text("orchestrator reply")]
    return Agent(LoopRunner(ScriptedProvider(script)), load_modules(), conn, "haiku",
                 services={"calendar": calendar})


def last_tool_result(agent) -> dict:
    return json.loads(agent.runner.provider.requests[-2]["messages"][-1]["content"][0]["content"])


def test_list_events(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_list_events", {"start": "2026-10-01"}))
    agent.ask("what's on thursday?")
    events = last_tool_result(agent)
    assert [e["title"] for e in events] == ["Team meeting", "Study block"]
    assert events[1]["start"] == "2026-10-01T17:00:00+02:00"


def test_single_event_is_created_right_away(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_create_events", {
        "events": [{"title": "Dentist", "start": "2026-10-02T09:30", "duration_min": 45}]}))
    reply = agent.ask("dentist friday 9:30")
    assert reply.pending == []
    created = [e for e in calendar.events.values() if e.title == "Dentist"][0]
    assert (created.start, created.end) == (at(2, 9, 30), at(2, 10, 15))


def test_several_events_wait_for_confirmation(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_create_events", {"events": [
        {"title": "Run", "start": "2026-10-03T08:00", "duration_min": 30},
        {"title": "Run", "start": "2026-10-05T08:00", "duration_min": 30},
    ]}))
    reply = agent.ask("runs on saturday and monday")
    assert len(calendar.events) == 2  # nothing created yet
    [pending] = reply.pending
    assert "Create 'Run' (Sat 2026-10-03 08:00-08:30)" in pending.summary
    assert last_tool_result(agent)["status"] == "awaiting_user_confirmation"

    assert agent.confirm(pending.id).startswith(f"Done #{pending.id}")
    assert len(calendar.events) == 4
    with pytest.raises(NotFound):
        agent.confirm(pending.id)  # can't run twice


def test_move_needs_confirmation_and_keeps_duration(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_move_events", {
        "moves": [{"event_id": "study", "new_start": "2026-10-02T18:00"}]}))
    reply = agent.ask("move my study block to friday 18:00")
    [pending] = reply.pending
    assert pending.summary == ("Move 'Study block' (Thu 2026-10-01 17:00-18:00) "
                               "to Fri 2026-10-02 18:00-19:00")
    assert calendar.get_event("study").start == at(1, 17)  # not moved yet

    agent.confirm(pending.id)
    assert (calendar.get_event("study").start, calendar.get_event("study").end) == (at(2, 18), at(2, 19))
    status = conn.execute("SELECT status, result FROM core_pending_actions").fetchone()
    assert status["status"] == "done" and "Study block" in status["result"]


def test_rejected_action_never_runs(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_move_events", {
        "moves": [{"event_id": "study", "new_start": "2026-10-02T18:00"}]}))
    [pending] = agent.ask("move it").pending
    agent.reject(pending.id)
    assert agent.pending_actions() == []
    assert calendar.get_event("study").start == at(1, 17)


def test_old_proposals_expire(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_move_events", {
        "moves": [{"event_id": "study", "new_start": "2026-10-02T18:00"}]}))
    [pending] = agent.ask("move it").pending
    conn.execute("UPDATE core_pending_actions SET created_at = datetime('now', '-25 hours')")
    conn.commit()
    with pytest.raises(NotFound, match="expired"):
        agent.confirm(pending.id)


def test_events_with_other_attendees_are_not_moved(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_move_events", {
        "moves": [{"event_id": "meeting", "new_start": "2026-10-02T10:00"}]}))
    reply = agent.ask("move the meeting")
    error = agent.runner.provider.requests[-2]["messages"][-1]["content"][0]
    assert error["is_error"] and "other attendees" in error["content"]
    assert reply.pending == []


def test_invalid_times_are_rejected(conn, calendar):
    agent = agent_with(conn, calendar, tool_call("calendar_create_events", {
        "events": [{"title": "Oops", "start": "2026-10-02T10:00", "end": "2026-10-02T09:00"}]}))
    agent.ask("oops")
    error = agent.runner.provider.requests[-2]["messages"][-1]["content"][0]
    assert error["is_error"] and "end must be after start" in error["content"]


def test_calendar_not_configured(conn):
    agent = agent_with(conn, None, tool_call("calendar_list_events", {"start": "2026-10-01"}))
    agent.ask("what's on?")
    error = agent.runner.provider.requests[-2]["messages"][-1]["content"][0]
    assert error["is_error"] and "calendar is not configured" in error["content"]


def test_nested_models_are_inlined_in_the_schema():
    schema = load_modules()["calendar"].tools["calendar_move_events"].schema()
    dumped = json.dumps(schema)
    assert "$ref" not in dumped and "$defs" not in dumped
    item = schema["input_schema"]["properties"]["moves"]["items"]
    assert set(item["properties"]) == {"event_id", "new_start", "new_end"}


# --- GoogleCalendar against a stubbed HTTP session ---

class Response:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code, self._payload, self.text = status, payload or {}, json.dumps(payload or {})

    def json(self):
        return self._payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, timeout=None, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def google(*responses) -> tuple[GoogleCalendar, Session]:
    session = Session(*responses)
    return GoogleCalendar("someone@example.com", "unused.json", TZ, session=session), session


def test_google_list_follows_pages_and_parses_events():
    calendar, session = google(
        Response(200, {"items": [{"id": "a", "summary": "A",
                                  "start": {"dateTime": "2026-10-01T15:00:00Z"},
                                  "end": {"dateTime": "2026-10-01T16:00:00Z"},
                                  "attendees": [{"email": "me", "self": True}, {"email": "x"}]}],
                       "nextPageToken": "p2"}),
        Response(200, {"items": [{"id": "b", "start": {"date": "2026-10-02"}, "end": {"date": "2026-10-03"},
                                  "extendedProperties": {"private": {MARKER: "1"}}},
                                 {"id": "c", "status": "cancelled", "start": {"date": "2026-10-02"},
                                  "end": {"date": "2026-10-03"}}]}),
    )
    events = calendar.list_events(at(1, 0), at(3, 0))
    assert [e.id for e in events] == ["a", "b"]
    assert events[0].start == at(1, 17) and events[0].has_other_attendees
    assert events[1].all_day and events[1].title == "(no title)" and events[1].created_by_secretary
    assert "someone%40example.com" in session.requests[0][1]
    assert session.requests[1][2]["params"]["pageToken"] == "p2"


def test_google_create_marks_the_event_and_sends_no_invites():
    calendar, session = google(Response(200, {"id": "n", "summary": "X",
                                              "start": {"dateTime": "2026-10-02T09:00:00+02:00"},
                                              "end": {"dateTime": "2026-10-02T10:00:00+02:00"}}))
    calendar.create_event("X", at(2, 9), at(2, 10))
    method, _, kwargs = session.requests[0]
    assert method == "POST" and kwargs["params"] == {"sendUpdates": "none"}
    assert kwargs["json"]["extendedProperties"] == {"private": {MARKER: "1"}}
    assert kwargs["json"]["start"] == {"dateTime": "2026-10-02T09:00:00+02:00", "timeZone": "Europe/Berlin"}


def test_google_errors_are_readable():
    calendar, _ = google(Response(403, {"error": "forbidden"}))
    with pytest.raises(ToolError, match="shared with the service account"):
        calendar.list_events(at(1, 0), at(2, 0))
    calendar, _ = google(Response(404))
    with pytest.raises(NotFound):
        calendar.get_event("missing")
