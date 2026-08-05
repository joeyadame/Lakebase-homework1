"""
One-time setup script for the Databricks secret used by the app.

Run from a Databricks notebook terminal or another environment where the
Databricks SDK is authenticated:

    python setup_secrets.py
"""

import getpass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace


def ensure_scope(client: WorkspaceClient, scope: str) -> None:
    try:
        client.secrets.create_scope(scope=scope)
    except Exception as exc:
        message = str(exc).lower()
        if "already" not in message and "exists" not in message:
            raise


def main() -> None:
    client = WorkspaceClient()
    scope = "database"

    ensure_scope(client, scope)
    client.secrets.put_secret(
        scope=scope,
        key="lakebase-url",
        string_value=getpass.getpass("Paste your Lakebase connection URL: "),
    )
    client.secrets.put_acl(
        scope=scope,
        principal="users",
        permission=workspace.AclPermission.READ,
    )
    print("Stored secret database/lakebase-url.")


if __name__ == "__main__":
    main()
