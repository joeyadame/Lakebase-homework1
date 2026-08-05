"""
Lakebase (Databricks-managed Postgres) connection helper.

Connection order:
1. Standard PG* env vars plus PGPASSWORD, for local psql-style config.
2. LAKEBASE_URL/DATABASE_URL, for native-password local development.
3. Standard PG* env vars plus ENDPOINT_NAME, for Databricks Apps/Lakebase
   resources that generate short-lived OAuth database credentials.
4. Legacy LAKEBASE_DB_* resource vars, for older provisioned-database examples.
5. Databricks secret database/lakebase-url as a final fallback.
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


def _direct_database_url() -> str | None:
    return os.environ.get("LAKEBASE_URL") or os.environ.get("DATABASE_URL")


def _has_standard_pg_env() -> bool:
    required = ("PGHOST", "PGDATABASE", "PGUSER")
    return all(os.environ.get(name) for name in required)


def _standard_pg_params() -> dict:
    password = os.environ.get("PGPASSWORD")
    if not password:
        password = _generate_standard_pg_password()

    return {
        "host": os.environ["PGHOST"],
        "database": os.environ["PGDATABASE"],
        "user": os.environ["PGUSER"],
        "port": int(os.environ.get("PGPORT", "5432")),
        "password": password,
        "sslmode": os.environ.get("PGSSLMODE", "require"),
    }


def _generate_standard_pg_password() -> str:
    endpoint = os.environ.get("ENDPOINT_NAME") or os.environ.get("LAKEBASE_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "PGPASSWORD is not set. Set ENDPOINT_NAME to the Lakebase endpoint "
            "resource path so the app can generate a temporary database credential."
        )

    credential = _workspace_client().postgres.generate_database_credential(
        endpoint=endpoint,
    )
    return credential.token


def _has_legacy_lakebase_resource_env() -> bool:
    required = (
        "LAKEBASE_DB_PGHOST",
        "LAKEBASE_DB_PGDATABASE",
        "LAKEBASE_DB_PGUSER",
    )
    return all(os.environ.get(name) for name in required)


def _legacy_lakebase_resource_params() -> dict:
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
    direct_url = _direct_database_url()
    if _has_standard_pg_env() and os.environ.get("PGPASSWORD"):
        conn = psycopg2.connect(
            **_standard_pg_params(),
            cursor_factory=RealDictCursor,
        )
    elif direct_url:
        conn = psycopg2.connect(direct_url, cursor_factory=RealDictCursor)
    elif _has_standard_pg_env():
        conn = psycopg2.connect(
            **_standard_pg_params(),
            cursor_factory=RealDictCursor,
        )
    elif _has_legacy_lakebase_resource_env():
        conn = psycopg2.connect(
            **_legacy_lakebase_resource_params(),
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
    direct_url = _direct_database_url()
    if _has_standard_pg_env() and os.environ.get("PGPASSWORD"):
        params = _standard_pg_params()
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
    if direct_url:
        return create_engine(direct_url)
    if _has_standard_pg_env():
        params = _standard_pg_params()
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
    if _has_legacy_lakebase_resource_env():
        params = _legacy_lakebase_resource_params()
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
