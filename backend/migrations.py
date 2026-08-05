"""AmicoScript — versioned SQLite schema migrations.

Every schema change gets a numbered step in MIGRATIONS. The applied version is
recorded in the `schema_version` table, so a step runs exactly once per database.

Design rules:

* A step is a plain callable taking a SQLAlchemy connection. It runs inside the
  transaction that also bumps the version, so a failure leaves the database on
  the previous version rather than half-migrated.
* Steps must be idempotent where cheaply possible (``ADD COLUMN`` guarded by a
  ``PRAGMA table_info`` check), because databases created before this module
  existed already carry some of these columns.
* Failures are **loud**. The previous implementation wrapped every ``ALTER
  TABLE`` in ``except Exception: pass``, so a broken database looked healthy
  until a query failed much later with a confusing error.
"""
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from utils.logging_utils import get_logger

logger = get_logger("amicoscript.migrations")


class MigrationError(RuntimeError):
    """Raised when a migration step fails, with the step number attached."""

    def __init__(self, version: int, name: str, cause: Exception) -> None:
        super().__init__(f"Migration {version} ({name}) failed: {cause}")
        self.version = version
        self.name = name
        self.cause = cause


def _columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
    return {r[1] for r in rows}


def _tables(conn: Connection) -> set[str]:
    rows = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    ).fetchall()
    return {r[0] for r in rows}


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    """ADD COLUMN unless it already exists (older DBs may already have it)."""
    if table not in _tables(conn):
        return
    if column in _columns(conn, table):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------


def _m001_folder_color_code(conn: Connection) -> None:
    _add_column(conn, "folder", "color_code", "color_code TEXT DEFAULT '#6c63ff'")


def _m002_recording_alias(conn: Connection) -> None:
    _add_column(conn, "recording", "alias", "alias TEXT")


def _m003_transcript_fts(conn: Connection) -> None:
    """FTS5 index over transcript.full_text, kept in sync by triggers.

    ``content='transcript'`` makes this an external-content table: the text is
    not duplicated, FTS reads it back from `transcript` via the rowid.
    """
    conn.execute(text("""
        CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts
        USING fts5(full_text, content='transcript', content_rowid='rowid')
    """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS transcript_ai
        AFTER INSERT ON transcript BEGIN
            INSERT INTO transcript_fts(rowid, full_text)
            VALUES (new.rowid, new.full_text);
        END
    """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS transcript_ad
        AFTER DELETE ON transcript BEGIN
            INSERT INTO transcript_fts(transcript_fts, rowid, full_text)
            VALUES ('delete', old.rowid, old.full_text);
        END
    """))
    conn.execute(text("""
        CREATE TRIGGER IF NOT EXISTS transcript_au
        AFTER UPDATE ON transcript BEGIN
            INSERT INTO transcript_fts(transcript_fts, rowid, full_text)
            VALUES ('delete', old.rowid, old.full_text);
            INSERT INTO transcript_fts(rowid, full_text)
            VALUES (new.rowid, new.full_text);
        END
    """))


def _m004_backfill_indexes(conn: Connection) -> None:
    """Indexes for databases created before the SQLModel fields were indexed.

    An index whose column is missing is skipped rather than fatal: indexes are
    a performance detail, and refusing to open a library over one would be a
    worse outcome than a slower query.
    """
    wanted = [
        ("ix_recording_status", "recording", "status"),
        ("ix_recording_created_at", "recording", "created_at"),
        ("ix_transcript_recording_id", "transcript", "recording_id"),
        ("ix_transcript_created_at", "transcript", "created_at"),
    ]
    tables = _tables(conn)
    for index_name, table, column in wanted:
        if table not in tables or column not in _columns(conn, table):
            logger.warning(
                "Skipping index %s: %s.%s does not exist", index_name, table, column
            )
            continue
        conn.execute(
            text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})")
        )


def _m005_recording_source(conn: Connection) -> None:
    """Where a recording came from ('upload', 'url', 'meeting'), for auto-summary."""
    _add_column(conn, "recording", "source", "source TEXT DEFAULT 'upload'")


def _m006_analysis_auto_generated(conn: Connection) -> None:
    """Marks analyses the app created on its own (auto-summary), not the user."""
    _add_column(
        conn, "analysis", "auto_generated", "auto_generated INTEGER DEFAULT 0"
    )


def _m007_recording_interrupted_reason(conn: Connection) -> None:
    """Explains a recording left in the 'interrupted' state after a restart."""
    _add_column(conn, "recording", "status_detail", "status_detail TEXT")


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None]]] = [
    (1, "folder.color_code", _m001_folder_color_code),
    (2, "recording.alias", _m002_recording_alias),
    (3, "transcript_fts", _m003_transcript_fts),
    (4, "backfill_indexes", _m004_backfill_indexes),
    (5, "recording.source", _m005_recording_source),
    (6, "analysis.auto_generated", _m006_analysis_auto_generated),
    (7, "recording.status_detail", _m007_recording_interrupted_reason),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _ensure_version_table(conn: Connection) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at REAL NOT NULL,
            name TEXT NOT NULL DEFAULT ''
        )
    """))


def get_current_version(conn: Connection) -> int:
    _ensure_version_table(conn)
    row = conn.execute(text("SELECT MAX(version) FROM schema_version")).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(engine) -> int:
    """Apply every pending migration in order. Returns the resulting version.

    Raises MigrationError if a step fails — callers should surface this rather
    than continue against a database in an unknown shape.
    """
    import time

    with engine.begin() as conn:
        current = get_current_version(conn)

    if current > SCHEMA_VERSION:
        # Database written by a newer AmicoScript. Reading it with old code can
        # corrupt data, so refuse instead of guessing.
        raise MigrationError(
            current,
            "downgrade",
            RuntimeError(
                f"database schema version {current} is newer than this build "
                f"supports ({SCHEMA_VERSION}); upgrade AmicoScript to open it"
            ),
        )

    applied = current
    for version, name, step in MIGRATIONS:
        if version <= current:
            continue
        try:
            with engine.begin() as conn:
                step(conn)
                conn.execute(
                    text(
                        "INSERT INTO schema_version (version, applied_at, name) "
                        "VALUES (:v, :t, :n)"
                    ),
                    {"v": version, "t": time.time(), "n": name},
                )
        except Exception as exc:  # noqa: BLE001 — re-raised as MigrationError
            logger.error("Migration %s (%s) failed: %s", version, name, exc)
            raise MigrationError(version, name, exc) from exc
        logger.info("Applied migration %s (%s)", version, name)
        applied = version

    return applied
