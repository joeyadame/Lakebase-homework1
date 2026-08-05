"""
Lakebase (Databricks-managed Postgres) connection helper.

In Databricks Apps, the connection URL is read from a Databricks secret. For
local development, set LAKEBASE_URL in your environment or .env file.
"""

from __future__ import annotations

import base64
import os
import uuid
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

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


def _has_lakebase_resource_env() -> bool:
    required = (
        "LAKEBASE_DB_PGHOST",
        "LAKEBASE_DB_PGDATABASE",
        "LAKEBASE_DB_PGUSER",
    )
    return all(os.environ.get(name) for name in required)


def _lakebase_resource_params() -> dict:
    database = os.environ["LAKEBASE_DB_PGDATABASE"]
    credential = _workspace_client().database.generate_database_credential(
        request_id=str(uuid.uuid4()),
        instance_names=[database],
    )
    return {
        "host": os.environ["LAKEBASE_DB_PGHOST"],
        "database": database,
        "user": os.environ["LAKEBASE_DB_PGUSER"],
        "port": int(os.environ.get("LAKEBASE_DB_PGPORT", "5432")),
        "password": credential.token,
        "sslmode": "require",
    }


def _lakebase_secret_url() -> str:
    secret = _workspace_client().secrets.get_secret(scope=_SCOPE, key=_KEY)
    return _decode_secret(secret.value)


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with dict-like rows."""
    direct_url = os.environ.get("LAKEBASE_URL")
    if direct_url:
        conn = psycopg2.connect(direct_url, cursor_factory=RealDictCursor)
    elif _has_lakebase_resource_env():
        conn = psycopg2.connect(
            **_lakebase_resource_params(),
            cursor_factory=RealDictCursor,
        )
    else:
        conn = psycopg2.connect(_lakebase_secret_url(), cursor_factory=RealDictCursor)

    try:
        yield conn
    finally:
        conn.close()


def get_engine():
    """Return a SQLAlchemy engine for Lakebase."""
    direct_url = os.environ.get("LAKEBASE_URL")
    if direct_url:
        return create_engine(direct_url)
    if _has_lakebase_resource_env():
        params = _lakebase_resource_params()
        url = URL.create(
            "postgresql+psycopg2",
            username=params["user"],
            password=params["password"],
            host=params["host"],
            port=params["port"],
            database=params["database"],
            query={"sslmode": params["sslmode"]},
        )
        return create_engine(url)
    return create_engine(_lakebase_secret_url())


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
