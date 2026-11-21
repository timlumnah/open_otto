import sqlite3

import pytest

from core import db


def write(dirpath, name, sql):
    (dirpath / name).write_text(sql)


# ---- discovery ----

def test_real_migrations_discovered_in_order():
    versions = [m.version for m in db.discover_migrations()]
    assert versions == sorted(versions)
    assert versions[0] == 1


def test_bad_filename_rejected(tmp_path):
    write(tmp_path, "core.sql", "SELECT 1;")
    with pytest.raises(db.MigrationError, match="bad migration filename"):
        db.discover_migrations(tmp_path)


def test_duplicate_version_rejected(tmp_path):
    write(tmp_path, "001_a.sql", "SELECT 1;")
    write(tmp_path, "001_b.sql", "SELECT 1;")
    with pytest.raises(db.MigrationError, match="duplicate migration version 1"):
        db.discover_migrations(tmp_path)


# ---- applying ----

def test_fresh_database_gets_all_migrations(empty_db):
    assert db.applied_versions(empty_db) == {m.version for m in db.discover_migrations()}


def test_migrate_is_idempotent(empty_db):
    assert db.migrate(empty_db) == []


def test_applies_in_version_order(tmp_path):
    write(tmp_path, "002_second.sql", "CREATE TABLE b (id INTEGER, a_id INTEGER REFERENCES a(id));")
    write(tmp_path, "001_first.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);"
          "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    conn = db.connect()
    assert db.migrate(conn, tmp_path) == [1, 2]
    assert db.applied_versions(conn) == {1, 2}


def test_runner_records_version_when_script_does_not(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    write(tmp_path, "002_more.sql", "CREATE TABLE t (id INTEGER);")
    conn = db.connect()
    db.migrate(conn, tmp_path)
    names = dict(conn.execute("SELECT version, name FROM schema_migrations").fetchall())
    assert names == {1: "core", 2: "more"}


def test_later_migration_applies_on_top_of_existing(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    conn = db.connect()
    db.migrate(conn, tmp_path)
    write(tmp_path, "002_more.sql", "CREATE TABLE t (id INTEGER);")
    assert db.migrate(conn, tmp_path) == [2]


# ---- failure handling ----

def test_failed_migration_rolls_back_completely(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    write(tmp_path, "002_bad.sql", "CREATE TABLE half_done (id INTEGER); SELECT * FROM no_such_table;")
    conn = db.connect()
    with pytest.raises(db.MigrationError, match="002_bad.sql failed"):
        db.migrate(conn, tmp_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "half_done" not in tables
    assert db.applied_versions(conn) == {1}


def test_failure_stops_later_migrations(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    write(tmp_path, "002_bad.sql", "SELECT * FROM no_such_table;")
    write(tmp_path, "003_after.sql", "CREATE TABLE after_it (id INTEGER);")
    conn = db.connect()
    with pytest.raises(db.MigrationError):
        db.migrate(conn, tmp_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "after_it" not in tables


def test_database_ahead_of_files_is_an_error(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    write(tmp_path, "002_more.sql", "CREATE TABLE t (id INTEGER);")
    conn = db.connect()
    db.migrate(conn, tmp_path)
    (tmp_path / "002_more.sql").unlink()
    with pytest.raises(db.MigrationError, match="no file on disk"):
        db.migrate(conn, tmp_path)


def test_out_of_order_migration_refused(tmp_path):
    write(tmp_path, "001_core.sql",
          "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    write(tmp_path, "003_later.sql", "CREATE TABLE later (id INTEGER);")
    conn = db.connect()
    db.migrate(conn, tmp_path)
    write(tmp_path, "002_late_arrival.sql", "CREATE TABLE sneaky (id INTEGER);")
    with pytest.raises(db.MigrationError, match="out of order"):
        db.migrate(conn, tmp_path)


def test_failed_seed_rolls_back(staged_db, tmp_path):
    bad = tmp_path / "bad.sql"
    bad.write_text(
        "INSERT INTO audit_log (actor, action, entity_type) VALUES ('t', 'a', 'x');"
        "SELECT * FROM nope;"
    )
    before = staged_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    with pytest.raises(db.MigrationError):
        db.run_seed(staged_db, bad)
    after = staged_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert before == after


def test_connect_enables_foreign_keys():
    conn = db.connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert isinstance(conn, sqlite3.Connection)
