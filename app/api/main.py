"""HTTP API and the PWA that uses it.

There is no login: the app is only reachable through the user's tailnet. Requests
that change anything must carry the `X-Secretary: 1` header. Browsers only let
other sites send custom headers after a CORS preflight, which this app never
approves, so a web page opened on the phone cannot drive the API (CSRF).
"""

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import Agent, build_agent, open_agent_db
from app.agent.orchestrator import PendingAction, Reply
from app.api import conversations
from app.db import connect
from app.db.migrations import migrate
from app.db.retention import purge_archived
from app.llm import LLMError
from app.sdk.errors import NotFound
from app.voice import Transcriber, VoiceUnavailable, get_transcriber

STATIC = Path(__file__).parent / "static"
CSRF_HEADER = "x-secretary"
MAX_AUDIO_BYTES = 15 * 1024 * 1024
PURGE_EVERY_S = 24 * 3600
MAX_CELL_CHARS = 300  # long values (chat histories, notes) are cut in the data view

log = logging.getLogger("secretary")


def _purge() -> None:
    conn = connect()
    try:
        deleted = purge_archived(conn)
    finally:
        conn.close()
    if deleted:
        log.info("purged rows archived for 30 days: %s", deleted)


async def _purge_daily() -> None:
    while True:
        try:
            await asyncio.to_thread(_purge)
        except Exception:  # keep the app running; the next run tries again
            log.exception("purge of archived rows failed")
        await asyncio.sleep(PURGE_EVERY_S)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates the database in WAL mode (needed by Litestream) and applies pending migrations.
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()
    purger = asyncio.create_task(_purge_daily())
    yield
    purger.cancel()


app = FastAPI(title="Secretary", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def require_app_header(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        return JSONResponse({"detail": "missing X-Secretary header"}, status_code=403)
    return await call_next(request)


# --- Dependencies (overridden in tests) ---

def get_agent():
    conn = open_agent_db()
    try:
        yield build_agent(conn)
    finally:
        conn.close()


def get_voice() -> Transcriber:
    return get_transcriber()


def get_db():
    """A read-only connection for the data views."""
    conn = connect(check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


# --- Schemas ---

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


class PendingOut(BaseModel):
    id: int
    summary: str


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    pending: list[PendingOut]
    tool_calls: int
    input_tokens: int
    output_tokens: int
    models: list[str]
    transcript: str | None = None


class DecisionRequest(BaseModel):
    conversation_id: int | None = None


class DecisionResponse(BaseModel):
    message: str


class TableInfo(BaseModel):
    name: str
    rows: int
    by_status: dict[str, int]  # count per status (or stage); empty if the table has none


class TablePage(BaseModel):
    name: str
    columns: list[str]
    rows: list[list]
    total: int


def _pending_out(actions: list[PendingAction]) -> list[PendingOut]:
    return [PendingOut(id=a.id, summary=a.summary) for a in actions]


def _chat(agent: Agent, text: str, conversation_id: int | None) -> ChatResponse:
    conn = agent.conn
    if conversation_id is None:
        conversation_id = conversations.create(conn)
    try:
        history = conversations.load(conn, conversation_id)
        reply: Reply = agent.ask(text, history)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except LLMError as exc:
        raise HTTPException(503, str(exc)) from None
    conversations.save(conn, conversation_id, reply.history)
    return ChatResponse(
        conversation_id=conversation_id,
        reply=reply.text,
        pending=_pending_out(reply.pending),
        tool_calls=len(reply.tool_calls),
        input_tokens=reply.usage.total_input_tokens,
        output_tokens=reply.usage.output_tokens,
        models=sorted(reply.models),
    )


# --- Routes ---

@app.get("/health")
def health() -> dict:
    conn = connect()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC / "dashboard.html")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    # Served from the root so the worker controls the whole app.
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, agent: Agent = Depends(get_agent)) -> ChatResponse:
    return _chat(agent, request.text.strip(), request.conversation_id)


@app.post("/api/voice", response_model=ChatResponse)
def voice(
    audio: UploadFile = File(...),
    conversation_id: int | None = Form(None),
    agent: Agent = Depends(get_agent),
    transcriber: Transcriber = Depends(get_voice),
) -> ChatResponse:
    suffix = Path(audio.filename or "").suffix or ".webm"
    fd, path = tempfile.mkstemp(prefix="secretary-voice-", suffix=suffix)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := audio.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(413, "recording too long")
                out.write(chunk)
        try:
            transcript = transcriber.transcribe(path)
        except VoiceUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
    finally:
        os.unlink(path)  # the audio is never kept
    if not transcript:
        raise HTTPException(422, "no speech recognized")
    response = _chat(agent, transcript, conversation_id)
    response.transcript = transcript
    return response


@app.get("/api/pending", response_model=list[PendingOut])
def pending(agent: Agent = Depends(get_agent)) -> list[PendingOut]:
    return _pending_out(agent.pending_actions())


def _decide(agent: Agent, action_id: int, request: DecisionRequest, confirm: bool) -> DecisionResponse:
    try:
        message = agent.confirm(action_id) if confirm else agent.reject(action_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from None
    if request.conversation_id is not None:
        try:
            conversations.append_note(agent.conn, request.conversation_id, message)
        except NotFound:
            pass  # the decision stands even if the chat it came from is gone
    return DecisionResponse(message=message)


@app.post("/api/pending/{action_id}/confirm", response_model=DecisionResponse)
def confirm(action_id: int, request: DecisionRequest, agent: Agent = Depends(get_agent)) -> DecisionResponse:
    return _decide(agent, action_id, request, confirm=True)


@app.post("/api/pending/{action_id}/reject", response_model=DecisionResponse)
def reject(action_id: int, request: DecisionRequest, agent: Agent = Depends(get_agent)) -> DecisionResponse:
    return _decide(agent, action_id, request, confirm=False)


# --- Data views (read-only) ---

def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND substr(name, 1, 1) != '_' "  # _litestream_*, _backup_probe
        "AND name != 'schema_migrations' ORDER BY name"
    )
    return [row[0] for row in rows]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _cell(value):
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "…"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


@app.get("/api/tables", response_model=list[TableInfo])
def tables(conn: sqlite3.Connection = Depends(get_db)) -> list[TableInfo]:
    result = []
    for table in _tables(conn):
        rows = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        by_status = {}
        state = next((c for c in ("status", "stage") if c in _columns(conn, table)), None)
        if state:
            by_status = dict(conn.execute(
                f'SELECT {state}, COUNT(*) FROM "{table}" GROUP BY {state} ORDER BY COUNT(*) DESC').fetchall())
        result.append(TableInfo(name=table, rows=rows, by_status=by_status))
    return result


@app.get("/api/tables/{name}", response_model=TablePage)
def table_rows(
    name: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
) -> TablePage:
    if name not in _tables(conn):  # the name is only interpolated after this check
        raise HTTPException(404, f"unknown table {name}")
    cursor = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))
    return TablePage(
        name=name,
        columns=[d[0] for d in cursor.description],
        rows=[[_cell(v) for v in row] for row in cursor],
        total=conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0],
    )


@app.get("/api/logs.csv", include_in_schema=False)
def token_log_csv(conn: sqlite3.Connection = Depends(get_db)) -> Response:
    """The agent log (one row per request) as CSV, for spreadsheets."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "timestamp_utc", "model", "input_tokens", "output_tokens",
                     "tool_calls", "tools", "input"])
    for row in conn.execute("SELECT * FROM core_agent_log ORDER BY id"):
        try:
            calls = json.loads(row["tools_called"] or "[]")
        except json.JSONDecodeError:
            calls = []
        tools = " ".join(f"{c.get('module')}.{c.get('tool')}" for c in calls if isinstance(c, dict))
        writer.writerow([row["id"], row["ts"], row["model"], row["input_tokens"], row["output_tokens"],
                         len(calls), tools, row["input"]])
    filename = f"secretary-tokens-{date.today():%Y%m%d}.csv"
    # The BOM makes Excel read the file as UTF-8 (accents).
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
