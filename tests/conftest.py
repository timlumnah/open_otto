import pytest

from core import db


@pytest.fixture
def empty_db():
    """Fresh in-memory database with the real migrations applied, no data."""
    conn = db.connect()
    db.migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def staged_db(empty_db):
    """Migrations plus the required pack stages."""
    db.run_seed(empty_db, db.DEFAULT_SEEDS_DIR / "001_pack_stages.sql")
    return empty_db


@pytest.fixture
def demo_db(staged_db):
    """Migrations, pack stages, and the fictional gigs demo data."""
    db.run_seed(staged_db, db.DEFAULT_SEEDS_DIR / "demo_gigs.sql")
    return staged_db


def stage_id(conn, pack, key):
    return conn.execute(
        "SELECT id FROM pipeline_stages WHERE pack = ? AND key = ?", (pack, key)
    ).fetchone()[0]
