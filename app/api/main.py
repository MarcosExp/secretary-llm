from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import connect


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Creates the database file in WAL mode so Litestream can start replicating.
    connect().close()
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
