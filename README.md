# Lakebase Support Ticket App

This Databricks App uses Lakebase as the operational database for an internal
support ticket system. It is adapted from the `EcZachly/databricks-lakebase-app-day-1`
template and replaces the sample Massive API workflow with support-ticket
tables, API routes, and a browser UI.

## What It Does

- Creates the required `tickets` and `ticket_messages` Lakebase tables.
- Enforces a foreign key from `ticket_messages.ticket_id` to `tickets.ticket_id`.
- Lets users view, filter, create, update, message, and delete support tickets.
- Stores all ticket and message changes in Lakebase, so refreshes load persisted data.
- Adds bonus fields and views for priority, status filtering, validation, and stats.

This version intentionally does not seed sample data because the homework note
for this repo said to ignore the sample-data requirement.

## Files

- `app.py` - Flask app with the ticket API and UI routes.
- `lakebase.py` - Lakebase Postgres connection helper.
- `templates/index.html` - Ticket dashboard UI.
- `setup_secrets.py` - One-time helper for storing the Lakebase URL in Databricks secrets.
- `app.yaml` - Databricks Apps deployment config.
- `.env.example` - Local development environment template.

## Lakebase Schema

The app creates these tables automatically before the first API read or write:

```sql
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY DEFAULT md5(random()::text || clock_timestamp()::text),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'low',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ticket_messages (
    message_id TEXT PRIMARY KEY DEFAULT md5(random()::text || clock_timestamp()::text),
    ticket_id TEXT NOT NULL REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    message_text TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Configure Lakebase

You can use this GitHub repo URL as the Databricks App source:

```text
https://github.com/joeyadame/Lakebase-homework1.git
```

Create a Lakebase instance in Databricks and create a native-password role for
the app. Copy the role's full Postgres connection URL. It should look like:

```text
postgresql://<role>:<password>@<host>.database.cloud.databricks.com:5432/databricks_postgres?sslmode=require
```

Store that URL in a Databricks secret:

```bash
python setup_secrets.py
```

If the URL you paste does not include a password, `setup_secrets.py` prompts
for the password separately and stores a complete connection URL in the secret.

By default, the app reads secret `database/lakebase-url` when a Lakebase app
starts. You can override the scope/key with `LAKEBASE_SECRET_SCOPE` and
`LAKEBASE_SECRET_KEY`, but the defaults match `setup_secrets.py` and
`app.yaml`.

## Run Locally

For local development, copy `.env.example` to `.env` and set `LAKEBASE_URL`:

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000`.

## Deploy As A Databricks App

1. Push this repo to GitHub.
2. In Databricks, create or open a Git folder for this repo.
3. Create a Databricks App and point it at the Git folder.
4. Deploy the app. `app.yaml` runs `python app.py` and points the app at the
   `database/lakebase-url` secret created by `setup_secrets.py`.
5. Open the app and create a ticket. Refresh the page to confirm the ticket,
   message, and status changes persisted in Lakebase.

## Deployment Troubleshooting

If Databricks fails before building with:

```text
Sparse checkout pattern must a valid folder path without leading slash
```

the source checkout settings are pointing at `/` or another invalid sparse
checkout value. This repo's app files live at the repository root, so create the
Git folder without sparse checkout enabled. If your Databricks UI requires an
app source folder/path, leave sparse checkout off and point the app at the Git
folder root rather than entering `/`.

## API

- `GET /healthz`
- `GET /api/tickets?status=open&q=password`
- `POST /api/tickets`
- `GET /api/tickets/<ticket_id>`
- `POST /api/tickets/<ticket_id>/messages`
- `PATCH /api/tickets/<ticket_id>/status`
- `DELETE /api/tickets/<ticket_id>`
- `GET /api/stats`
