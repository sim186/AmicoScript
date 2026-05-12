"""AmicoScript — database engine, session dependency, and FTS5 setup.

Usage in FastAPI route handlers:
    from db import get_session
    from fastapi import Depends
    from sqlmodel import Session

    @app.get("/api/...")
    def my_route(session: Session = Depends(get_session)):
        ...

Usage in background threads (worker):
    from db import new_session
    with new_session() as session:
        ...
"""
from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from config import DB_PATH

# check_same_thread=False is required because FastAPI and the worker thread
# both open sessions against the same engine.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables and set up the FTS5 virtual table + sync triggers."""
    # Import models so SQLModel.metadata is populated before create_all.
    import models  # noqa: F401

    SQLModel.metadata.create_all(engine)

    with engine.begin() as conn:
        # Ensure `folder` table has a color_code column for older DBs.
        try:
            rows = conn.execute(text("PRAGMA table_info('folder')")).fetchall()
            col_names = [r[1] for r in rows]
            if "color_code" not in col_names:
                conn.execute(text("ALTER TABLE folder ADD COLUMN color_code TEXT DEFAULT '#6c63ff'"))
        except Exception:
            pass

        # Ensure `recording` table has an alias column for older DBs.
        try:
            rows = conn.execute(text("PRAGMA table_info('recording')")).fetchall()
            col_names = [r[1] for r in rows]
            if "alias" not in col_names:
                conn.execute(text("ALTER TABLE recording ADD COLUMN alias TEXT"))
        except Exception:
            pass
        # FTS5 content table — does NOT duplicate full_text; reads from
        # the transcript table via the triggers defined below.
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts
            USING fts5(full_text, content='transcript', content_rowid='rowid')
        """))

        # Triggers keep the FTS index in sync with the transcript table.
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

        # Backfill indexes for older DBs that predate indexed SQLModel fields.
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recording_status ON recording(status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recording_created_at ON recording(created_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_transcript_recording_id ON transcript(recording_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_transcript_created_at ON transcript(created_at)"))


def get_session():
    """FastAPI dependency — yields a session and commits on success, rolls back on error."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def new_session():
    """Context manager for use in background threads (not FastAPI requests)."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
