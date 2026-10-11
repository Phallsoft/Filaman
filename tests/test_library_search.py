from app.database import SessionLocal, engine, init_db
from app.models import LibraryModel, User
from app.services import library_search as ls
from app.services import library_taxonomy as tx
from tests.helpers import do_setup


def _setup(client):
    do_setup(client)
    db = SessionLocal()
    return db, db.query(User).first().id


def test_search_matches_title_tag_and_category_path(client):
    db, uid = _setup(client)
    cat = tx.create_category(db, uid, "Workshop")
    sub = tx.create_category(db, uid, "Holders", cat.id)
    m = LibraryModel(user_id=uid, title="Bit rack", category_id=sub.id,
                     tags=tx.get_or_create_tags(db, uid, ["gift"]))
    other = LibraryModel(user_id=uid, title="Dragon")
    db.add_all([m, other])
    db.flush()
    ls.reindex(db, [m.id, other.id])
    db.commit()
    assert ls.search_ids(db, "bit") == {m.id}
    assert ls.search_ids(db, "gift") == {m.id}
    assert ls.search_ids(db, "workshop") == {m.id}
    assert ls.search_ids(db, "drag") == {other.id}
    assert ls.search_ids(db, "   ") is None
    db.close()


def test_category_rename_reflected_after_reindex(client):
    db, uid = _setup(client)
    cat = tx.create_category(db, uid, "Toys")
    m = LibraryModel(user_id=uid, title="Top", category_id=cat.id)
    db.add(m)
    db.flush()
    ls.reindex(db, [m.id])
    tx.rename_category(db, cat, "Fidgets")
    ls.reindex(db, tx.affected_model_ids_for_categories(db, [cat.id]))
    db.commit()
    assert ls.search_ids(db, "fidgets") == {m.id}
    assert ls.search_ids(db, "toys") == set()
    db.close()


def test_search_input_with_fts_syntax_does_not_error(client):
    db, uid = _setup(client)
    m = LibraryModel(user_id=uid, title='Hook "XL"')
    db.add(m)
    db.flush()
    ls.reindex(db, [m.id])
    db.commit()
    for q in ['"', '*', 'AND', '(hook', 'hook -', 'NEAR(', "x' OR 1=1 --"]:
        ls.search_ids(db, q)  # must not raise
    assert ls.search_ids(db, '"hook"') == {m.id}
    db.close()


def test_deleted_model_removed_from_index(client):
    db, uid = _setup(client)
    m = LibraryModel(user_id=uid, title="Ephemeral")
    db.add(m)
    db.flush()
    ls.reindex(db, [m.id])
    mid = m.id
    db.delete(m)
    db.flush()
    ls.reindex(db, [mid])
    db.commit()
    assert ls.search_ids(db, "ephemeral") == set()
    db.close()


def test_init_db_rebuilds_missing_index(client):
    db, uid = _setup(client)
    m = LibraryModel(user_id=uid, title="Orphan index")
    db.add(m)
    db.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE library_search")
    init_db()
    assert ls.search_ids(db, "orphan") == {m.id}
    db.close()
