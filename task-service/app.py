"""TaskBoard task-service.

REST microservice that owns the ``tasks`` data. It is the only component
allowed to talk to PostgreSQL.

Endpoints:
    GET    /tasks          list all tasks
    POST   /tasks          create a task           {"title": str}
    GET    /tasks/<id>     get one task
    PUT    /tasks/<id>     update a task           {"title"?: str, "done"?: bool}
    DELETE /tasks/<id>     delete a task
    GET    /health         liveness/readiness probe
"""

import logging
import os
import socket
import time
from typing import Any

import psycopg
from flask import Flask, jsonify, request
from psycopg.rows import dict_row

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("task-service")

HOSTNAME = socket.gethostname()
MAX_TITLE_LEN = 200

DB_CONNINFO = psycopg.conninfo.make_conninfo(
    host=os.getenv("DB_HOST", "postgres"),
    port=os.getenv("DB_PORT", "5432"),
    dbname=os.getenv("DB_NAME", "taskboard"),
    user=os.getenv("DB_USER", "taskboard"),
    password=os.getenv("DB_PASSWORD", ""),
    connect_timeout=3,
)

app = Flask(__name__)


def get_conn() -> psycopg.Connection:
    """Open a new database connection that returns rows as dicts."""
    return psycopg.connect(DB_CONNINFO, row_factory=dict_row, autocommit=True)


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create the schema, retrying until PostgreSQL is reachable.

    The database may start after this service, so transient connection
    failures at startup are expected.
    """
    for attempt in range(1, retries + 1):
        try:
            with get_conn() as conn, conn.transaction():
                # Several replicas/workers start at once; serialize schema creation
                # because concurrent CREATE TABLE IF NOT EXISTS can still collide.
                conn.execute("SELECT pg_advisory_xact_lock(4242)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id         SERIAL PRIMARY KEY,
                        title      VARCHAR(200) NOT NULL,
                        done       BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            log.info("Database ready")
            return
        except psycopg.OperationalError as exc:
            log.warning("DB not ready (attempt %d/%d): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database")


def serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row to a JSON-friendly dict."""
    return {**row, "created_at": row["created_at"].isoformat()}


def respond(payload: Any, status: int = 200):
    """Wrap a payload with the serving pod name (shows load balancing)."""
    return jsonify({"data": payload, "served_by": HOSTNAME}), status


def error(message: str, status: int):
    """Return a JSON error response."""
    return jsonify({"error": message, "served_by": HOSTNAME}), status


def validate_title(value: Any) -> str | None:
    """Return a cleaned title, or None if invalid."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not 1 <= len(value) <= MAX_TITLE_LEN:
        return None
    return value


@app.get("/health")
def health():
    """Report healthy only if the database answers."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return jsonify({"status": "ok", "served_by": HOSTNAME})
    except psycopg.Error as exc:
        log.error("Health check failed: %s", exc)
        return error("database unavailable", 503)


@app.get("/tasks")
def list_tasks():
    """Return all tasks, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, done, created_at FROM tasks ORDER BY id DESC"
        ).fetchall()
    return respond([serialize(r) for r in rows])


@app.post("/tasks")
def create_task():
    """Create a task from {"title": "..."}."""
    body = request.get_json(silent=True) or {}
    title = validate_title(body.get("title"))
    if title is None:
        return error(f"'title' must be a string of 1-{MAX_TITLE_LEN} characters", 400)
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO tasks (title) VALUES (%s) RETURNING id, title, done, created_at",
            (title,),
        ).fetchone()
    log.info("Created task %s", row["id"])
    return respond(serialize(row), 201)


@app.get("/tasks/<int:task_id>")
def get_task(task_id: int):
    """Return a single task."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, done, created_at FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
    if row is None:
        return error("task not found", 404)
    return respond(serialize(row))


@app.put("/tasks/<int:task_id>")
def update_task(task_id: int):
    """Update title and/or done flag of a task."""
    body = request.get_json(silent=True) or {}
    fields, values = [], []
    if "title" in body:
        title = validate_title(body["title"])
        if title is None:
            return error(f"'title' must be a string of 1-{MAX_TITLE_LEN} characters", 400)
        fields.append("title = %s")
        values.append(title)
    if "done" in body:
        if not isinstance(body["done"], bool):
            return error("'done' must be a boolean", 400)
        fields.append("done = %s")
        values.append(body["done"])
    if not fields:
        return error("nothing to update: provide 'title' and/or 'done'", 400)

    # Column names come from the fixed list above; values are parameterized.
    query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s RETURNING id, title, done, created_at"
    with get_conn() as conn:
        row = conn.execute(query, (*values, task_id)).fetchone()
    if row is None:
        return error("task not found", 404)
    return respond(serialize(row))


@app.delete("/tasks/<int:task_id>")
def delete_task(task_id: int):
    """Delete a task."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    if cur.rowcount == 0:
        return error("task not found", 404)
    log.info("Deleted task %s", task_id)
    return "", 204


@app.errorhandler(psycopg.OperationalError)
def db_down(exc: Exception):
    """Map lost DB connectivity to 503 so callers can retry."""
    log.error("Database error: %s", exc)
    return error("database unavailable", 503)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
