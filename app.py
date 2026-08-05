"""
Databricks App for an internal support ticket system.

The app serves a small Flask UI/API and stores operational data in Lakebase
(Databricks-managed Postgres) through lakebase.py.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request

import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("support-app")

app = Flask(__name__)
_w: WorkspaceClient | None = None

STATUSES = ("open", "in_progress", "resolved", "closed")
PRIORITIES = ("low", "medium", "high", "urgent")
MAX_TITLE_LENGTH = 140
MAX_MESSAGE_LENGTH = 5000

_SCHEMA_READY = False


def _workspace_client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def _ticket_tables_exist() -> bool:
    rows = lakebase.run_query(
        """
        SELECT
            to_regclass('tickets') IS NOT NULL AS tickets_exists,
            to_regclass('ticket_messages') IS NOT NULL AS messages_exists
        """
    )
    row = rows[0]
    return bool(row["tickets_exists"] and row["messages_exists"])


def _run_optional_schema_statement(sql: str, label: str) -> None:
    try:
        lakebase.run_write(sql)
    except Exception as err:
        message = str(err).lower()
        if "must be owner" in message or "permission denied" in message:
            logger.warning("Skipping optional schema step %s: %s", label, err)
            return
        raise


def ensure_schema() -> None:
    """Create the Lakebase tables needed by the support app."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    if not _ticket_tables_exist():
        lakebase.run_write(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY
                    DEFAULT md5(random()::text || clock_timestamp()::text),
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
                priority TEXT NOT NULL DEFAULT 'low'
                    CHECK (priority IN ('low', 'medium', 'normal', 'high', 'urgent')),
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        lakebase.run_write(
            """
            CREATE TABLE IF NOT EXISTS ticket_messages (
                message_id TEXT PRIMARY KEY
                    DEFAULT md5(random()::text || clock_timestamp()::text),
                ticket_id TEXT NOT NULL
                    REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                message_text TEXT NOT NULL CHECK (length(trim(message_text)) > 0),
                author TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    _run_optional_schema_statement(
        """
        CREATE INDEX IF NOT EXISTS idx_tickets_status_updated_at
            ON tickets (status, updated_at DESC)
        """,
        "idx_tickets_status_updated_at",
    )
    _run_optional_schema_statement(
        """
        CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket_created
            ON ticket_messages (ticket_id, created_at ASC)
        """,
        "idx_ticket_messages_ticket_created",
    )
    _SCHEMA_READY = True


def _current_user_email() -> str:
    """Resolve the current Databricks user, with a local-dev fallback."""
    header_email = request.headers.get("X-Forwarded-Email")
    if header_email:
        return header_email.strip()
    try:
        return _workspace_client().current_user.me().user_name
    except Exception:
        return "local.user@example.com"


def _json_ready(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_row(row: Any) -> dict[str, Any]:
    return {key: _json_ready(value) for key, value in dict(row).items()}


def _serialize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [_serialize_row(row) for row in rows]


def _payload() -> dict[str, Any]:
    return (request.get_json(silent=True) or {}) if request.is_json else {}


def _clean_text(value: Any, *, collapse: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if collapse:
        value = " ".join(value.split())
    return value


def _error(message: str, status_code: int = 400):
    return jsonify({"error": message}), status_code


def _validate_status(status: str) -> str | None:
    status = _clean_text(status).lower()
    return status if status in STATUSES else None


def _validate_priority(priority: str) -> str | None:
    priority = _clean_text(priority).lower()
    return priority if priority in PRIORITIES else None


def _fetch_ticket(ticket_id: str) -> dict[str, Any] | None:
    rows = lakebase.run_query(
        """
        SELECT
            t.ticket_id,
            t.title,
            t.status,
            t.priority,
            t.created_by,
            t.created_at,
            t.updated_at,
            COUNT(m.message_id)::int AS message_count,
            MAX(m.created_at) AS latest_message_at
        FROM tickets t
        LEFT JOIN ticket_messages m ON m.ticket_id = t.ticket_id
        WHERE t.ticket_id = %s
        GROUP BY
            t.ticket_id,
            t.title,
            t.status,
            t.priority,
            t.created_by,
            t.created_at,
            t.updated_at
        """,
        (ticket_id,),
    )
    return _serialize_row(rows[0]) if rows else None


@app.errorhandler(Exception)
def handle_exception(err):
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    return render_template(
        "index.html",
        statuses=STATUSES,
        priorities=PRIORITIES,
        current_user=_current_user_email(),
    )


@app.route("/api/tickets", methods=["GET"])
def list_tickets():
    ensure_schema()

    status_filter = _clean_text(request.args.get("status", "all")).lower() or "all"
    search = _clean_text(request.args.get("q", ""), collapse=True)

    conditions = []
    params: list[Any] = []

    if status_filter != "all":
        status = _validate_status(status_filter)
        if status is None:
            return _error(f"Status must be one of: {', '.join(STATUSES)}")
        conditions.append("t.status = %s")
        params.append(status)

    if search:
        conditions.append("t.title ILIKE %s")
        params.append(f"%{search}%")

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = lakebase.run_query(
        f"""
        SELECT
            t.ticket_id,
            t.title,
            t.status,
            t.priority,
            t.created_by,
            t.created_at,
            t.updated_at,
            COUNT(m.message_id)::int AS message_count,
            MAX(m.created_at) AS latest_message_at
        FROM tickets t
        LEFT JOIN ticket_messages m ON m.ticket_id = t.ticket_id
        {where_sql}
        GROUP BY
            t.ticket_id,
            t.title,
            t.status,
            t.priority,
            t.created_by,
            t.created_at,
            t.updated_at
        ORDER BY
            CASE t.status
                WHEN 'open' THEN 1
                WHEN 'in_progress' THEN 2
                WHEN 'resolved' THEN 3
                ELSE 4
            END,
            t.updated_at DESC
        """,
        tuple(params),
    )
    return jsonify({"tickets": _serialize_rows(rows)})


@app.route("/api/tickets", methods=["POST"])
def create_ticket():
    ensure_schema()
    data = _payload()

    title = _clean_text(data.get("title"), collapse=True)
    status = _validate_status(data.get("status", "open"))
    priority = _validate_priority(data.get("priority", "low"))
    created_by = _clean_text(data.get("created_by"), collapse=True) or _current_user_email()
    message_text = _clean_text(
        data.get("message_text") or data.get("initial_message"),
        collapse=False,
    )

    if not title:
        return _error("Ticket title is required.")
    if len(title) > MAX_TITLE_LENGTH:
        return _error(f"Ticket title must be {MAX_TITLE_LENGTH} characters or fewer.")
    if status is None:
        return _error(f"Status must be one of: {', '.join(STATUSES)}")
    if priority is None:
        return _error(f"Priority must be one of: {', '.join(PRIORITIES)}")
    if len(message_text) > MAX_MESSAGE_LENGTH:
        return _error(f"Message must be {MAX_MESSAGE_LENGTH} characters or fewer.")

    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tickets (
                    title, status, priority, created_by, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, now(), now())
                RETURNING
                    ticket_id,
                    title,
                    status,
                    priority,
                    created_by,
                    created_at,
                    updated_at
                """,
                (title, status, priority, created_by),
            )
            ticket = cur.fetchone()

            message = None
            if message_text:
                cur.execute(
                    """
                    INSERT INTO ticket_messages (
                        ticket_id, message_text, author, created_at
                    )
                    VALUES (%s, %s, %s, now())
                    RETURNING
                        message_id,
                        ticket_id,
                        message_text,
                        author,
                        created_at
                    """,
                    (ticket["ticket_id"], message_text, created_by),
                )
                message = cur.fetchone()

            conn.commit()

    response = {
        "ticket": _serialize_row(ticket),
        "message": _serialize_row(message) if message else None,
    }
    return jsonify(response), 201


@app.route("/api/tickets/<ticket_id>", methods=["GET"])
def get_ticket(ticket_id: str):
    ensure_schema()
    ticket = _fetch_ticket(ticket_id)
    if ticket is None:
        return _error("Ticket not found.", 404)

    messages = lakebase.run_query(
        """
        SELECT message_id, ticket_id, message_text, author, created_at
        FROM ticket_messages
        WHERE ticket_id = %s
        ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return jsonify({"ticket": ticket, "messages": _serialize_rows(messages)})


@app.route("/api/tickets/<ticket_id>/messages", methods=["POST"])
def add_ticket_message(ticket_id: str):
    ensure_schema()
    data = _payload()

    message_text = _clean_text(data.get("message_text"), collapse=False)
    author = _clean_text(data.get("author"), collapse=True) or _current_user_email()

    if not message_text:
        return _error("Message text is required.")
    if len(message_text) > MAX_MESSAGE_LENGTH:
        return _error(f"Message must be {MAX_MESSAGE_LENGTH} characters or fewer.")

    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticket_id FROM tickets WHERE ticket_id = %s", (ticket_id,))
            if cur.fetchone() is None:
                return _error("Ticket not found.", 404)

            cur.execute(
                """
                INSERT INTO ticket_messages (
                    ticket_id, message_text, author, created_at
                )
                VALUES (%s, %s, %s, now())
                RETURNING message_id, ticket_id, message_text, author, created_at
                """,
                (ticket_id, message_text, author),
            )
            message = cur.fetchone()
            cur.execute(
                "UPDATE tickets SET updated_at = now() WHERE ticket_id = %s",
                (ticket_id,),
            )
            conn.commit()

    return jsonify({"message": _serialize_row(message)}), 201


@app.route("/api/tickets/<ticket_id>/status", methods=["PATCH"])
def update_ticket_status(ticket_id: str):
    ensure_schema()
    data = _payload()
    status = _validate_status(data.get("status", ""))

    if status is None:
        return _error(f"Status must be one of: {', '.join(STATUSES)}")

    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tickets
                SET status = %s, updated_at = now()
                WHERE ticket_id = %s
                RETURNING ticket_id
                """,
                (status, ticket_id),
            )
            updated = cur.fetchone()
            conn.commit()

    if updated is None:
        return _error("Ticket not found.", 404)

    return jsonify({"ticket": _fetch_ticket(ticket_id)})


@app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id: str):
    ensure_schema()
    row_count = lakebase.run_write(
        "DELETE FROM tickets WHERE ticket_id = %s",
        (ticket_id,),
    )
    if row_count == 0:
        return _error("Ticket not found.", 404)
    return jsonify({"deleted": True, "ticket_id": ticket_id})


@app.route("/api/stats")
def ticket_stats():
    ensure_schema()
    rows = lakebase.run_query(
        """
        SELECT
            COUNT(*)::int AS total_tickets,
            COUNT(*) FILTER (WHERE status = 'open')::int AS open_tickets,
            COUNT(*) FILTER (WHERE status = 'in_progress')::int AS in_progress_tickets,
            COUNT(*) FILTER (WHERE status = 'resolved')::int AS resolved_tickets,
            (
                SELECT COUNT(*)::int
                FROM ticket_messages
            ) AS total_messages
        FROM tickets
        """
    )
    return jsonify({"stats": _serialize_row(rows[0])})


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", os.getenv("PORT", "8000")))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
