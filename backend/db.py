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

from sqlmodel import Session, SQLModel, create_engine

from config import DB_PATH

# check_same_thread=False is required because FastAPI and the worker thread
# both open sessions against the same engine.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db(target_engine=None) -> int:
    """Create tables and apply pending migrations. Returns the schema version.

    Schema changes live in backend/migrations.py as numbered steps recorded in
    the `schema_version` table — never as ad-hoc ALTERs here. A failing
    migration raises MigrationError instead of being swallowed, because
    continuing against a half-known schema produces far more confusing errors
    later on.
    """
    from migrations import run_migrations

    # Import models so SQLModel.metadata is populated before create_all.
    import models  # noqa: F401

    eng = target_engine if target_engine is not None else engine
    SQLModel.metadata.create_all(eng)
    return run_migrations(eng)


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
