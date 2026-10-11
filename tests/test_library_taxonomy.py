import pytest

from app.database import SessionLocal
from app.models import LibraryModel, LibraryTag, User
from app.services import library_taxonomy as tx
from tests.helpers import do_setup


@pytest.fixture()
def db(client):
    do_setup(client)
    with SessionLocal() as session:
        yield session


def _uid(db):
    return db.query(User).first().id


def test_create_rejects_duplicate_sibling_case_insensitive(db):
    uid = _uid(db)
    tx.create_category(db, uid, "Workshop")
    with pytest.raises(tx.TaxonomyError):
        tx.create_category(db, uid, "workshop")
    parent = tx.create_category(db, uid, "Toys")
    tx.create_category(db, uid, "Workshop", parent.id)  # different parent is fine


def test_tree_rows_counts_include_descendants(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    c = tx.create_category(db, uid, "C", b.id)
    rows = tx.tree_rows(tx.user_categories(db, uid), {a.id: 1, b.id: 2, c.id: 3})
    assert [(r["category"].name, r["depth"], r["count"]) for r in rows] == [
        ("A", 0, 6), ("B", 1, 5), ("C", 2, 3)
    ]
    assert rows[2]["path"] == "A › B › C"


def test_move_into_own_descendant_rejected(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    with pytest.raises(tx.TaxonomyError):
        tx.move_category(db, a, b.id)
    with pytest.raises(tx.TaxonomyError):
        tx.move_category(db, a, a.id)
    tx.move_category(db, b, None)
    assert b.parent_id is None


def test_delete_category_rehomes_models_and_children(db):
    uid = _uid(db)
    a = tx.create_category(db, uid, "A")
    b = tx.create_category(db, uid, "B", a.id)
    c = tx.create_category(db, uid, "C", b.id)
    m = LibraryModel(user_id=uid, title="m", category_id=b.id)
    db.add(m)
    db.flush()
    tx.delete_category(db, b)
    db.flush()
    db.refresh(m)
    assert m.category_id is None
    assert c.parent_id == a.id


def test_parse_tag_names():
    assert tx.parse_tag_names(" gift, Printed ,gift,, needs-supports ") == ["gift", "Printed", "needs-supports"]
    assert tx.parse_tag_names(None) == []


def test_rename_tag_merges_on_collision(db):
    uid = _uid(db)
    gift, present = tx.get_or_create_tags(db, uid, ["gift", "present"])
    m1 = LibraryModel(user_id=uid, title="one", tags=[gift])
    m2 = LibraryModel(user_id=uid, title="two", tags=[gift, present])
    db.add_all([m1, m2])
    db.flush()
    survivor = tx.rename_tag(db, present, "Gift")
    db.flush()
    db.refresh(m1)
    db.refresh(m2)
    assert survivor.id == gift.id
    assert db.query(LibraryTag).filter_by(user_id=uid).count() == 1
    assert [t.name for t in m1.tags] == ["gift"] and [t.name for t in m2.tags] == ["gift"]


def test_rename_tag_case_only_change_keeps_tag(db):
    uid = _uid(db)
    [gift] = tx.get_or_create_tags(db, uid, ["gift"])
    assert tx.rename_tag(db, gift, "Gift").id == gift.id
    assert gift.name == "Gift"
