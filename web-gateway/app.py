"""TaskBoard web-gateway.

Single entry point for clients. It serves the web UI
and exposes /api/*, which it fulfils by calling the task-service REST API.
It holds no state and no database credentials.

Endpoints:
    GET    /                  web UI
    GET    /api/tasks         list tasks          -> task-service GET /tasks
    POST   /api/tasks         create task         -> task-service POST /tasks
    PUT    /api/tasks/<id>    update task         -> task-service PUT /tasks/<id>
    DELETE /api/tasks/<id>    delete task         -> task-service DELETE /tasks/<id>
    GET    /api/stats         statistics, aggregated here from GET /tasks
    GET    /health            liveness/readiness probe (does not depend on task-service)
"""

import logging
import os
import socket
import time

import requests
from flask import Flask, Response, jsonify, render_template, request

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("web-gateway")

HOSTNAME = socket.gethostname()
TASK_SERVICE_URL = os.getenv("TASK_SERVICE_URL", "http://task-service:8000").rstrip("/")
TIMEOUT_S = float(os.getenv("UPSTREAM_TIMEOUT_S", "2"))
RETRIES = int(os.getenv("UPSTREAM_RETRIES", "3"))
BACKOFF_S = 0.2

app = Flask(__name__)
session = requests.Session()


class UpstreamUnavailable(Exception):
    """Raised when task-service cannot be reached after all retries."""


def call_task_service(method: str, path: str, json_body: dict | None = None) -> requests.Response:
    """Call task-service, retrying transient failures (Busy Signal pattern).

    Retries on connection errors, timeouts and HTTP 503 with a short linear
    backoff. Retry count and delay are kept small because users are waiting.
    Non-transient responses (2xx/4xx/other 5xx) are returned immediately.
    """
    url = f"{TASK_SERVICE_URL}{path}"
    last_problem = ""
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.request(method, url, json=json_body, timeout=TIMEOUT_S)
            if resp.status_code != 503:
                return resp
            last_problem = "HTTP 503"
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_problem = type(exc).__name__
        log.warning("task-service %s %s failed (%s), attempt %d/%d",
                    method, path, last_problem, attempt, RETRIES)
        if attempt < RETRIES:
            time.sleep(BACKOFF_S * attempt)
    raise UpstreamUnavailable(last_problem)


def relay(resp: requests.Response) -> Response:
    """Pass an upstream response through, adding the gateway pod name."""
    if resp.status_code == 204:
        return Response(status=204, headers={"X-Gateway": HOSTNAME})
    payload = resp.json()
    payload["gateway"] = HOSTNAME
    return jsonify(payload), resp.status_code


@app.errorhandler(UpstreamUnavailable)
def upstream_down(exc: UpstreamUnavailable):
    """Tell the user the backend is busy instead of showing a stack trace."""
    return jsonify({"error": "task-service is unavailable, please retry shortly",
                    "detail": str(exc), "gateway": HOSTNAME}), 503


@app.get("/")
def index():
    """Serve the single-page UI."""
    return render_template("index.html")


@app.get("/health")
def health():
    """Gateway health only; task-service outages must not kill gateway pods."""
    return jsonify({"status": "ok", "served_by": HOSTNAME})


@app.get("/api/tasks")
def list_tasks():
    """List tasks via task-service."""
    return relay(call_task_service("GET", "/tasks"))


@app.post("/api/tasks")
def create_task():
    """Create a task via task-service (validation happens there)."""
    return relay(call_task_service("POST", "/tasks", request.get_json(silent=True) or {}))


@app.put("/api/tasks/<int:task_id>")
def update_task(task_id: int):
    """Update a task via task-service."""
    return relay(call_task_service("PUT", f"/tasks/{task_id}", request.get_json(silent=True) or {}))


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id: int):
    """Delete a task via task-service."""
    return relay(call_task_service("DELETE", f"/tasks/{task_id}"))


@app.get("/api/stats")
def stats():
    """Aggregate statistics from the task-service task list."""
    resp = call_task_service("GET", "/tasks")
    if resp.status_code != 200:
        return relay(resp)
    body = resp.json()
    tasks = body["data"]
    total = len(tasks)
    done = sum(1 for t in tasks if t["done"])
    return jsonify({
        "data": {
            "total": total,
            "done": done,
            "pending": total - done,
            "completion_rate": round(done / total, 3) if total else 0.0,
        },
        "served_by": body.get("served_by"),
        "gateway": HOSTNAME,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
