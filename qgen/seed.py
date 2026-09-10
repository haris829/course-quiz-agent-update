"""Copying the course catalogue and question bank into an empty database.

A deployment starts with a database that has no courses in it, and a system that answers from
course material is useless without the material. This exports the reference tables from one
database and loads them into another.

WHY IT MATCHES COLUMNS RATHER THAN ASSUMING THEM
-------------------------------------------------
The same tables exist in more than one shape - the deployed ``qc_courses`` was made by an
earlier import and has only a code and a title. An INSERT naming a column the target has not got
fails outright, so each table is loaded with the intersection of what the export carries and
what the target actually has. A column missing at the far end is dropped, not invented.

WHAT IT WILL NOT DO
-------------------
It never updates or deletes. Every insert is ``ON CONFLICT DO NOTHING``, so loading twice is a
no-op and loading into a database that already has rows adds only what is missing. This copies
reference data into an empty deployment; it is not a synchroniser and must not be mistaken for
one.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import psycopg

#: The tables that hold what the system reads, in the order they must be loaded - a child table
#: cannot be inserted before the rows it points at.
REFERENCE_TABLES = (
    "qc_courses",
    "qb_topics",
    "qb_questions",
    "qb_question_options",
    "qb_question_topics",
)

#: Where the export lives inside the package by default.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "reference_data.json.gz"


def export(conn: psycopg.Connection, path: Path = DEFAULT_PATH) -> dict[str, int]:
    """Write the reference tables to a compressed file. Read-only on the source."""
    payload: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in REFERENCE_TABLES:
            cur.execute(f"SELECT * FROM {table}")
            rows = [dict(record) for record in cur.fetchall()]
            payload[table] = rows
            counts[table] = len(rows)
    path.write_bytes(gzip.compress(json.dumps(payload, default=str).encode("utf-8")))
    return counts


def _target_columns(conn: psycopg.Connection, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
        )
        return {record["column_name"] for record in cur.fetchall()}


#: Above this many courses, the catalogue is taken to be loaded already.
#:
#: The load runs on every start, because a deployment has no other moment to run it in. It is
#: idempotent, but three thousand no-op inserts on every restart is waste, and a container that
#: takes ten seconds longer to answer its health check for no reason is a container that looks
#: broken. A count is one query.
ALREADY_LOADED_ABOVE = 5


def already_loaded(conn: psycopg.Connection) -> bool:
    """Whether this database already has a catalogue worth the name."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = 'qc_courses'"
        )
        if not cur.fetchone()["n"]:
            return False
        cur.execute("SELECT count(*) AS n FROM qc_courses")
        return cur.fetchone()["n"] > ALREADY_LOADED_ABOVE


def load(conn: psycopg.Connection, path: Path = DEFAULT_PATH) -> dict[str, str]:
    """Load the export into this database. Idempotent, additive, never destructive."""
    if not path.is_file():
        raise FileNotFoundError(f"no export at {path}")
    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))

    report: dict[str, str] = {}
    for table in REFERENCE_TABLES:
        rows = payload.get(table) or []
        if not rows:
            report[table] = "nothing to load"
            continue

        available = _target_columns(conn, table)
        if not available:
            report[table] = "no such table here - skipped"
            continue

        columns = [c for c in rows[0] if c in available]
        dropped = [c for c in rows[0] if c not in available]
        placeholders = ", ".join(["%s"] * len(columns))
        names = ", ".join(f'"{c}"' for c in columns)
        sql = f"INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

        with conn.cursor() as cur:
            cur.executemany(sql, [[row.get(c) for c in columns] for row in rows])
        note = f"{len(rows)} rows"
        if dropped:
            note += f" (columns not present here, dropped: {', '.join(dropped)})"
        report[table] = note
    return report
