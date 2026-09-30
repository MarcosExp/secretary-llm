import csv
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.agent.loop import LoopRunner
from app.agent.orchestrator import Agent
from app.api import conversations, main
from app.db import connect
from app.db.migrations import migrate
from app.llm.base import LLMError
from app.llm.fake import ScriptedProvider, text, tool_call
from app.sdk.modules import load_modules
from modules.calendar.client import Event, FakeCalendar

HEADERS = {"X-Secretary": "1"}
TZ = ZoneInfo("Europe/Berlin")


class Setup:
    def __init__(self, db_path):
        self.db_path = db_path
        self.provider = ScriptedProvider([])
        self.calendar = FakeCalendar([
            Event("study", "Study block", datetime(2026, 10, 1, 17, tzinfo=TZ), datetime(2026, 10, 1, 18, tzinfo=TZ)),
        ])
        self.transcripts: list[str] = []
        self.audio_paths: list[str] = []

    def script(self, *steps):
        self.provider.steps = list(steps)

    def agent(self):
        conn = connect(self.db_path, cached_statements=0, check_same_thread=False)
        migrate(conn)
        try:
            yield Agent(LoopRunner(self.provider), load_modules(), conn, "haiku",
                        services={"calendar": self.calendar})
        finally:
            conn.close()

    def transcribe(self, path):
        assert os.path.exists(path)  # the upload is on disk while transcribing...
        self.audio_paths.append(path)
        return self.transcripts.pop(0)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETARY_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("SECRETARY_TIMEZONE", "Europe/Berlin")
    return Setup(tmp_path / "db.sqlite")


@pytest.fixture
def client(setup):
    main.app.dependency_overrides[main.get_agent] = setup.agent
    main.app.dependency_overrides[main.get_voice] = lambda: setup
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


def chat(client, message, conversation_id=None):
    return client.post("/api/chat", headers=HEADERS,
                       json={"text": message, "conversation_id": conversation_id})


def test_pwa_files_are_served(client):
    assert "<title>Secretary</title>" in client.get("/").text
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert client.get("/sw.js").headers["content-type"].startswith("application/javascript")
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/icons/icon-512.png").headers["content-type"] == "image/png"


def test_writes_without_the_app_header_are_refused(client):
    response = client.post("/api/chat", json={"text": "hi"})
    assert response.status_code == 403
    assert client.post("/api/pending/1/confirm", json={}).status_code == 403


def test_chat_keeps_the_conversation(client, setup):
    setup.script(text("Hello!"), text("You said hello."))
    first = chat(client, "hello").json()
    assert first["reply"] == "Hello!" and first["pending"] == []
    second = chat(client, "what did I say?", first["conversation_id"]).json()
    assert second["conversation_id"] == first["conversation_id"]
    history = setup.provider.requests[1]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert "hello" in history[0]["content"]


def test_unknown_conversation_is_404(client, setup):
    setup.script()
    assert chat(client, "hi", 999).status_code == 404


def test_model_errors_become_503(client, setup):
    def fail(request):
        raise LLMError("Claude subscription usage limit reached; try again later")
    setup.script(fail)
    response = chat(client, "hi")
    assert response.status_code == 503 and "usage limit" in response.json()["detail"]


def test_pending_action_confirmed_from_the_app(client, setup):
    setup.script(
        tool_call("delegate_to_calendar", {"task": "move study block to friday 18:00"}),
        tool_call("calendar_move_events", {"moves": [{"event_id": "study", "new_start": "2026-10-02T18:00"}]}),
        text("Proposed #1."), text("I need your confirmation to move it."),
    )
    response = chat(client, "move my study block to friday 18:00").json()
    [pending] = response["pending"]
    assert "Move 'Study block'" in pending["summary"]
    assert client.get("/api/pending").json() == [pending]

    decision = client.post(f"/api/pending/{pending['id']}/confirm", headers=HEADERS,
                           json={"conversation_id": response["conversation_id"]})
    assert decision.json()["message"].startswith(f"Done #{pending['id']}")
    assert setup.calendar.get_event("study").start.hour == 18
    assert client.get("/api/pending").json() == []

    conn = connect(setup.db_path)
    history = conversations.load(conn, response["conversation_id"])
    assert history[-1]["content"].startswith("<app_note>Done #")
    assert client.post(f"/api/pending/{pending['id']}/reject", headers=HEADERS, json={}).status_code == 404


def test_voice_is_transcribed_answered_and_deleted(client, setup):
    setup.transcripts = ["add milk to the list"]
    setup.script(text("Added."))
    response = client.post("/api/voice", headers=HEADERS,
                           files={"audio": ("recording.webm", b"fake-opus-bytes", "audio/webm")})
    body = response.json()
    assert body["transcript"] == "add milk to the list" and body["reply"] == "Added."
    assert "add milk to the list" in setup.provider.requests[0]["messages"][0]["content"]
    assert setup.audio_paths[0].endswith(".webm")
    assert not os.path.exists(setup.audio_paths[0])  # ...and gone afterwards


def test_voice_limits(client, setup, monkeypatch):
    monkeypatch.setattr(main, "MAX_AUDIO_BYTES", 10)
    response = client.post("/api/voice", headers=HEADERS,
                           files={"audio": ("r.webm", b"x" * 11, "audio/webm")})
    assert response.status_code == 413

    monkeypatch.setattr(main, "MAX_AUDIO_BYTES", 1000)
    setup.transcripts = [""]
    response = client.post("/api/voice", headers=HEADERS, files={"audio": ("r.webm", b"x", "audio/webm")})
    assert response.status_code == 422


def test_history_is_trimmed_at_turn_boundaries():
    history = []
    for i in range(15):
        history += [
            {"role": "user", "content": f"turn {i}"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
    trimmed = conversations.trim(history, keep_turns=10)
    assert trimmed[0] == {"role": "user", "content": "turn 5"}
    assert len(trimmed) == 40


def test_token_log_downloads_as_csv(client, setup):
    setup.script(
        tool_call("delegate_to_core", {"task": "add ink"}),
        tool_call("add_task", {"title": "Buy ink, black"}),
        text("Added."),
        text("Añadido."),
    )
    chat(client, "añade tinta")
    response = client.get("/api/logs.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    body = response.content.decode("utf-8")
    assert body.startswith("\ufeff")  # BOM, so Excel reads accents correctly
    header, row = list(csv.reader(body.lstrip("\ufeff").splitlines()))
    assert header[:6] == ["id", "timestamp_utc", "model", "input_tokens", "output_tokens", "tool_calls"]
    assert row[5] == "1" and row[6] == "core.add_task" and row[7] == "añade tinta"


def test_data_views_list_tables_and_rows(client, setup):
    conn = connect(setup.db_path)
    conn.execute("CREATE TABLE _backup_probe (id INTEGER)")  # written by scripts/restore-test.sh
    conn.executemany("INSERT INTO core_tasks (title, status) VALUES (?, ?)",
                     [("a", "todo"), ("b", "todo"), ("c", "archived"), ("x" * 1000, "done")])
    conn.commit()
    conn.close()
    assert "<title>Secretary · Data</title>" in client.get("/dashboard").text

    tables = {t["name"]: t for t in client.get("/api/tables").json()}
    assert "schema_migrations" not in tables and "_backup_probe" not in tables
    assert tables["core_tasks"]["rows"] == 4
    assert tables["core_tasks"]["by_status"] == {"todo": 2, "archived": 1, "done": 1}
    assert tables["jobs_applications"]["by_status"] == {}  # empty, but it has a stage column

    page = client.get("/api/tables/core_tasks", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 4 and len(page["rows"]) == 2
    title = page["rows"][0][page["columns"].index("title")]
    assert title.endswith("…") and len(title) == 301  # newest first, long values cut
    assert "archived_at" in page["columns"]


def test_data_views_are_read_only_and_reject_unknown_tables(client):
    assert client.get("/api/tables/sqlite_master").status_code == 404
    assert client.get('/api/tables/core_tasks"; DROP TABLE core_tasks; --').status_code == 404
    assert client.get("/api/tables/core_tasks", params={"limit": 10_000}).status_code == 422
    connection = main.get_db()
    conn = next(connection)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO core_areas (name) VALUES ('x')")
    connection.close()
