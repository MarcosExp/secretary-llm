"""Chat histories for the web interface, stored in core_conversations."""

import json
import sqlite3

from app.sdk.errors import NotFound

KEEP_TURNS = 10  # user turns kept in the history sent to the model


def create(conn: sqlite3.Connection) -> int:
    with conn:
        return conn.execute("INSERT INTO core_conversations DEFAULT VALUES").lastrowid


def load(conn: sqlite3.Connection, conversation_id: int) -> list[dict]:
    row = conn.execute(
        "SELECT history FROM core_conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"conversation {conversation_id} not found")
    return json.loads(row["history"])


def save(conn: sqlite3.Connection, conversation_id: int, history: list[dict]) -> None:
    with conn:
        conn.execute(
            "UPDATE core_conversations SET history = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(trim(history), ensure_ascii=False), conversation_id),
        )


def append_note(conn: sqlite3.Connection, conversation_id: int, note: str) -> None:
    """Tell the model about something that happened outside the chat (a confirmation)."""
    history = load(conn, conversation_id)
    history.append({"role": "user", "content": f"<app_note>{note}</app_note>"})
    save(conn, conversation_id, history)


def trim(history: list[dict], keep_turns: int = KEEP_TURNS) -> list[dict]:
    """Keep the last `keep_turns` turns. A turn starts at a user message written as text,
    so the result never begins with a tool result the model would not understand."""
    starts = [
        i for i, message in enumerate(history)
        if message["role"] == "user" and isinstance(message["content"], str)
    ]
    if len(starts) <= keep_turns:
        return history
    return history[starts[-keep_turns]:]
