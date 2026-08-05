"""
Print a temporary Lakebase database password/token.

Use this for local testing or psql access before deployment. The deployed app
does not need a pasted password when ENDPOINT_NAME is available; it generates a
short-lived credential at runtime.

Examples:
    python get_lakebase_password.py --endpoint-name projects/bootcamp/branches/production/endpoints/default
    python get_lakebase_password.py --format env
"""

from __future__ import annotations

import argparse
import os
import shlex
import uuid
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from databricks.sdk import WorkspaceClient


def _workspace_client() -> WorkspaceClient:
    return WorkspaceClient()


def _generate_password(endpoint_name: str | None, instance_name: str | None) -> str:
    client = _workspace_client()

    if endpoint_name:
        credential = client.postgres.generate_database_credential(endpoint=endpoint_name)
        return credential.token

    if instance_name:
        credential = client.database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[instance_name],
        )
        return credential.token

    raise SystemExit(
        "Set ENDPOINT_NAME or pass --endpoint-name. For older provisioned "
        "Lakebase examples, set DATABASE_INSTANCE_NAME or pass --instance-name."
    )


def _database_url(password: str) -> str:
    host = os.environ.get("PGHOST")
    database = os.environ.get("PGDATABASE", "databricks_postgres")
    user = os.environ.get("PGUSER")
    port = os.environ.get("PGPORT", "5432")
    sslmode = os.environ.get("PGSSLMODE", "require")

    if not host or not user:
        raise SystemExit("PGHOST and PGUSER are required when using --format url.")

    return (
        f"postgresql://{quote_plus(user)}:{quote_plus(password)}@"
        f"{host}:{port}/{database}?sslmode={sslmode}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a temporary Lakebase database password/token."
    )
    parser.add_argument(
        "--endpoint-name",
        default=os.environ.get("ENDPOINT_NAME") or os.environ.get("LAKEBASE_ENDPOINT"),
        help="Lakebase endpoint resource path, like projects/<project>/branches/<branch>/endpoints/<endpoint>.",
    )
    parser.add_argument(
        "--instance-name",
        default=os.environ.get("DATABASE_INSTANCE_NAME")
        or os.environ.get("LAKEBASE_DB_INSTANCE_NAME"),
        help="Legacy provisioned Lakebase instance name.",
    )
    parser.add_argument(
        "--format",
        choices=("password", "env", "url"),
        default="password",
        help="Output just the password, shell export lines, or a Postgres URL.",
    )
    args = parser.parse_args()

    password = _generate_password(args.endpoint_name, args.instance_name)

    if args.format == "password":
        print(password)
        return

    if args.format == "env":
        print(f"export PGPASSWORD={shlex.quote(password)}")
        if args.endpoint_name:
            print(f"export ENDPOINT_NAME={shlex.quote(args.endpoint_name)}")
        return

    print(_database_url(password))


if __name__ == "__main__":
    main()
