"""
Lakebase (Databricks-managed Postgres) connection helper.

The deployed Databricks App reads one secret, database/lakebase-url, that holds
the full Postgres connection URL for a native-password Lakebase role.
"""

from __future__ import annotations

import base64
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")
_w: WorkspaceClient | None = None


def _workspace_client() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def _decode_secret(value: str) -> str:
    try:
        return base64.b64decode(value).decode("utf-8")
    except Exception:
        return value


def _lakebase_url() -> str:
    direct_url = os.environ.get("LAKEBASE_URL")
    if direct_url:
        return direct_url

    secret = _workspace_client().secrets.get_secret(scope=_SCOPE, key=_KEY)
    return _decode_secret(secret.value)


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with dict-like rows."""
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def get_engine():
    """Return a SQLAlchemy engine for Lakebase."""
    return create_engine(_lakebase_url())


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE/DDL statement and return affected rows."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount
