import sqlite3

from app.agent.orchestrator import Agent, Reply
from app.config import load_config, orchestrator_model
from app.db import connect
from app.db.migrations import migrate
from app.llm import AgentRunner, get_runner
from app.sdk.modules import load_modules


def open_agent_db() -> sqlite3.Connection:
    """A migrated connection for the agent.

    Statement cache off (see app/sdk/access.py). Usable from other threads: the
    subscription runner calls tools from worker threads, one at a time.
    """
    conn = connect(cached_statements=0, check_same_thread=False)
    migrate(conn)
    return conn


def build_agent(conn: sqlite3.Connection, runner: AgentRunner | None = None) -> Agent:
    config = load_config()
    return Agent(
        runner=runner or get_runner(),
        modules=load_modules(config=config),
        conn=conn,
        model=orchestrator_model(config),
    )


__all__ = ["Agent", "Reply", "build_agent", "open_agent_db"]
