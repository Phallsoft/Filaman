import sqlite3

from app.migrations import migrate_multi_user

OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    hashed_password VARCHAR(128) NOT NULL
);
CREATE TABLE manufacturers (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    mfg_url VARCHAR(512)
);
CREATE TABLE material_types (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE
);
CREATE TABLE colors (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    color_code VARCHAR(7)
);
CREATE TABLE spools (
    id INTEGER NOT NULL PRIMARY KEY,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers (id),
    material_type_id INTEGER NOT NULL REFERENCES material_types (id),
    color_id INTEGER NOT NULL REFERENCES colors (id),
    sku VARCHAR(128),
    weight INTEGER NOT NULL,
    image_path VARCHAR(512),
    CONSTRAINT uq_spool_identity UNIQUE (manufacturer_id, material_type_id, color_id, weight, sku)
);
CREATE TABLE spool_inventory (
    spool_id INTEGER NOT NULL PRIMARY KEY REFERENCES spools (id),
    qty INTEGER NOT NULL
);
"""

OLD_DATA = """
INSERT INTO users (id, username, hashed_password) VALUES (1, 'admin', '$2b$12$hashhashhash');
INSERT INTO manufacturers (id, name, mfg_url) VALUES (1, 'Prusa', 'https://prusa3d.com');
INSERT INTO material_types (id, name) VALUES (1, 'PLA');
INSERT INTO colors (id, name, color_code) VALUES (1, 'Black', '#000000');
INSERT INTO spools (id, manufacturer_id, material_type_id, color_id, sku, weight, image_path)
    VALUES (7, 1, 1, 1, 'SKU-1', 1000, 'spools/abc.jpg');
INSERT INTO spool_inventory (spool_id, qty) VALUES (7, 3);
"""


def _make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA + OLD_DATA)
    conn.close()


def _q(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_migrates_single_user_db_to_admin_plus_jpaul(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)

    assert migrate_multi_user(db) is True

    users = _q(db, "SELECT id, username, hashed_password, is_admin FROM users ORDER BY id")
    assert users == [
        (1, "admin", "$2b$12$hashhashhash", 1),
        (2, "jpaul", "$2b$12$hashhashhash", 0),
    ]
    assert _q(db, "SELECT id, user_id, name, mfg_url FROM manufacturers") == [(1, 2, "Prusa", "https://prusa3d.com")]
    assert _q(db, "SELECT id, user_id, name FROM material_types") == [(1, 2, "PLA")]
    assert _q(db, "SELECT id, user_id, name, color_code FROM colors") == [(1, 2, "Black", "#000000")]
    assert _q(db, "SELECT id, user_id, manufacturer_id, sku, weight, image_path FROM spools") == [
        (7, 2, 1, "SKU-1", 1000, "spools/abc.jpg")
    ]
    assert _q(db, "SELECT spool_id, qty FROM spool_inventory") == [(7, 3)]
    # new unique constraints in place, old tables gone
    assert _q(db, "PRAGMA foreign_key_check") == []
    names = {r[0] for r in _q(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(n.endswith("_old") for n in names)
    sql = _q(db, "SELECT sql FROM sqlite_master WHERE name='spools'")[0][0]
    assert "user_id" in sql and "uq_spool_identity" in sql


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)
    migrate_multi_user(db)
    assert migrate_multi_user(db) is False
    assert _q(db, "SELECT count(*) FROM users") == [(2,)]


def test_migration_with_two_users_keeps_data_on_first_user(tmp_path):
    db = str(tmp_path / "old.db")
    _make_old_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO users (id, username, hashed_password) VALUES (2, 'other', 'h')")
    conn.commit()
    conn.close()
    migrate_multi_user(db)
    assert _q(db, "SELECT username FROM users WHERE username='jpaul'") == []
    assert _q(db, "SELECT user_id FROM spools") == [(1,)]
    assert _q(db, "SELECT is_admin FROM users ORDER BY id") == [(1,), (0,)]


def test_fresh_db_is_untouched(tmp_path):
    db = str(tmp_path / "fresh.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, hashed_password TEXT, is_admin BOOLEAN NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE spools (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)")
    conn.commit()
    conn.close()
    assert migrate_multi_user(db) is False
