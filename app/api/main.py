"""HTTP API and the PWA that uses it.

There is no login: the app is only reachable through the user's tailnet. Requests
that change anything must carry the `X-Secretary: 1` header. Browsers only let
other sites send custom headers after a CORS preflight, which this app never
approves, so a web page opened on the phone cannot drive the API (CSRF).
"""

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import Agent, build_agent, open_agent_db
from app.agent.orchestrator import PendingAction, Reply
from app.api import conversations
from app.db import connect
from app.db.migrations import migrate
from app.llm import LLMError
from app.sdk.errors import NotFound
from app.voice import Transcriber, VoiceUnavailable, get_transcriber

STATIC = Path(__file__).parent / "static"
CSRF_HEADER = "x-secretary"
MAX_AUDIO_BYTES = 15 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates the database in WAL mode (needed by Litestream) and applies pending migrations.
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()
    yield


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
