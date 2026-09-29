from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import connect
from app.db.migrations import migrate


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates the database in WAL mode (needed by Litestream) and applies pending migrations.
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Secretary", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    conn = connect()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {"status": "ok"}
