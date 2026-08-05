"""Tests for the versioned schema migration runner."""
import pytest
from sqlalchemy import text
from sqlmodel import create_engine


@pytest.fixture()
def fresh_engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}")


def _version(engine) -> int:
    from migrations import get_current_version

    with engine.begin() as conn:
        return get_current_version(conn)


def _columns(engine, table: str) -> set[str]:
    with engine.begin() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info('{table}')"))}


def test_migrations_bring_a_fresh_database_to_the_current_version(fresh_engine):
    import db
    import migrations

    assert db.init_db(fresh_engine) == migrations.SCHEMA_VERSION
    assert _version(fresh_engine) == migrations.SCHEMA_VERSION


def test_running_twice_applies_nothing_the_second_time(fresh_engine):
    import db
    import migrations

    db.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        before = conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()

    migrations.run_migrations(fresh_engine)
    with fresh_engine.begin() as conn:
        after = conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()

    assert before == after


def test_legacy_database_gains_the_new_columns(tmp_path):
    """A database from before this module existed must upgrade in place."""
    import migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        # The 1.x shape: no color_code, no alias, no source, no FTS table.
        conn.execute(text("CREATE TABLE folder (id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(text(
            "CREATE TABLE recording (id TEXT PRIMARY KEY, filename TEXT, "
            "file_path TEXT, status TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE transcript (id TEXT PRIMARY KEY, recording_id TEXT, "
            "full_text TEXT, json_data TEXT, created_at REAL)"
        ))
        conn.execute(text("CREATE TABLE analysis (id TEXT PRIMARY KEY, recording_id TEXT)"))
        conn.execute(text("INSERT INTO recording VALUES ('r1', 'a.mp3', '/tmp/a.mp3', 'done')"))

    assert migrations.run_migrations(engine) == migrations.SCHEMA_VERSION
    assert "color_code" in _columns(engine, "folder")
    assert {"alias", "source", "status_detail"} <= _columns(engine, "recording")
    assert "auto_generated" in _columns(engine, "analysis")

    # Existing data survives.
    with engine.begin() as conn:
        assert conn.execute(text("SELECT filename FROM recording")).scalar() == "a.mp3"


def test_fts_table_and_triggers_are_created(fresh_engine):
    import db

    db.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        names = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master"))
        }
    assert "transcript_fts" in names
    assert {"transcript_ai", "transcript_ad", "transcript_au"} <= names


def test_a_failing_step_raises_and_does_not_bump_the_version(fresh_engine, monkeypatch):
    """The old code swallowed migration failures; this asserts it no longer does."""
    import migrations

    def _boom(conn):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(
        migrations, "MIGRATIONS", [(1, "folder.color_code", migrations._m001_folder_color_code),
                                   (2, "explodes", _boom)]
    )
    with fresh_engine.begin() as conn:
        conn.execute(text("CREATE TABLE folder (id TEXT PRIMARY KEY, name TEXT)"))

    with pytest.raises(migrations.MigrationError) as excinfo:
        migrations.run_migrations(fresh_engine)

    assert excinfo.value.version == 2
    assert "disk on fire" in str(excinfo.value)
    # Step 1 committed, step 2 did not.
    assert _version(fresh_engine) == 1


def test_a_newer_database_is_refused_rather_than_guessed(fresh_engine):
    import db
    import migrations

    db.init_db(fresh_engine)
    with fresh_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO schema_version (version, applied_at, name) VALUES (:v, 0, 'future')"),
            {"v": migrations.SCHEMA_VERSION + 5},
        )

    with pytest.raises(migrations.MigrationError) as excinfo:
        migrations.run_migrations(fresh_engine)
    assert "newer" in str(excinfo.value)


def test_migration_numbers_are_unique_and_ordered():
    import migrations

    versions = [version for version, _, _ in migrations.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions[0] == 1
