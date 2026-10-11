import re
from io import BytesIO

from PIL import Image

from app.database import SessionLocal
from app.models import StoredFile
from tests.helpers import add_model, add_user, csrf, do_setup, login


def _png(color="red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, format="PNG")
    return buf.getvalue()


def _file_ids(client, mid):
    return [int(x) for x in re.findall(r'href="/models/files/(\d+)"', client.get(f"/models/{mid}").text)]


def _image_ids(client, mid):
    # The detail page repeats images in the thumbnail strip; keep first-seen order.
    seen = []
    for x in re.findall(r'src="/models/images/(\d+)"', client.get(f"/models/{mid}").text):
        if int(x) not in seen:
            seen.append(int(x))
    return seen


def test_upload_and_download_file(client):
    do_setup(client)
    mid = add_model(client, title="Cube", files=[("files", ("cube.stl", b"solid cube", "model/stl"))])
    [fid] = _file_ids(client, mid)
    r = client.get(f"/models/files/{fid}")
    assert r.status_code == 200 and r.content == b"solid cube"
    assert "attachment" in r.headers["content-disposition"] and "cube.stl" in r.headers["content-disposition"]


def test_upload_more_files_on_edit(client):
    do_setup(client)
    mid = add_model(client, title="Kit", files=[("files", ("a.stl", b"aaa", "model/stl"))])
    token = csrf(client, f"/models/{mid}/edit")
    client.post(f"/models/{mid}", data={"title": "Kit", "csrf_token": token},
                files=[("files", ("b.stl", b"bbb", "model/stl"))])
    assert len(_file_ids(client, mid)) == 2


def test_empty_file_part_is_ignored(client):
    do_setup(client)
    mid = add_model(client, title="Nothing", files=[("files", ("", b"", "application/octet-stream"))])
    assert _file_ids(client, mid) == []
    with SessionLocal() as db:
        assert db.query(StoredFile).count() == 0


def test_too_large_file_reported_but_model_saved(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MODEL_MAX_FILE_MB", 0)
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "Big", "csrf_token": token},
                    files=[("files", ("huge.stl", b"x" * 10, "model/stl"))])
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Big" in page and "huge.stl" in page  # flashed error names the file
    assert _file_ids(client, int(r.headers["location"].rsplit("/", 1)[1])) == []


def test_download_filename_is_sanitised(client):
    do_setup(client)
    mid = add_model(client, title="Evil", files=[("files", ('../../etc/pa"ss\nwd.stl', b"data", "model/stl"))])
    [fid] = _file_ids(client, mid)
    cd = client.get(f"/models/files/{fid}").headers["content-disposition"]
    assert ".." not in cd and "/" not in cd.split("filename", 1)[1] and "\n" not in cd


def test_rename_and_delete_file(client):
    do_setup(client)
    mid = add_model(client, title="Parts", files=[("files", ("a.stl", b"aaa", "model/stl"))])
    [fid] = _file_ids(client, mid)
    token = csrf(client, f"/models/{mid}/edit")
    client.post(f"/models/{mid}/files/{fid}/rename", data={"filename": "base.stl", "csrf_token": token})
    assert "base.stl" in client.get(f"/models/{mid}").text
    client.post(f"/models/{mid}/files/{fid}/delete", data={"csrf_token": token})
    assert _file_ids(client, mid) == []
    with SessionLocal() as db:
        assert db.query(StoredFile).count() == 0


def test_images_upload_cover_and_delete(client):
    do_setup(client)
    mid = add_model(client, title="Pics", files=[
        ("images", ("one.png", _png("red"), "image/png")),
        ("images", ("two.png", _png("blue"), "image/png")),
    ])
    ids = _image_ids(client, mid)
    assert len(ids) == 2
    r = client.get(f"/models/images/{ids[0]}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    token = csrf(client, f"/models/{mid}/edit")
    client.post(f"/models/{mid}/images/{ids[1]}/cover", data={"csrf_token": token})
    assert _image_ids(client, mid)[0] == ids[1]
    for iid in ids:
        client.post(f"/models/{mid}/images/{iid}/delete", data={"csrf_token": token})
    assert _image_ids(client, mid) == []
    with SessionLocal() as db:
        assert db.query(StoredFile).count() == 0


def test_unreadable_image_reported_not_fatal(client):
    do_setup(client)
    token = csrf(client, "/models/new")
    r = client.post("/models", data={"title": "Bad pic", "csrf_token": token},
                    files=[("images", ("broken.png", b"not an image", "image/png"))])
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Bad pic" in page and "broken.png" in page


def test_private_assets_hidden_from_other_user(client, client2):
    do_setup(client)
    add_user(client, "bob")
    login(client2, "bob", "password123")
    mid = add_model(client, title="Mine", files=[
        ("files", ("a.stl", b"aaa", "model/stl")), ("images", ("p.png", _png(), "image/png")),
    ])
    [fid] = _file_ids(client, mid)
    [iid] = _image_ids(client, mid)
    assert client2.get(f"/models/files/{fid}").status_code == 404
    assert client2.get(f"/models/images/{iid}").status_code == 404
    token = csrf(client2, "/models")
    client2.post(f"/models/{mid}/files/{fid}/delete", data={"csrf_token": token})
    client2.post(f"/models/{mid}/images/{iid}/delete", data={"csrf_token": token})
    assert _file_ids(client, mid) == [fid]
    assert _image_ids(client, mid) == [iid]


def test_file_id_must_belong_to_model_in_url(client):
    do_setup(client)
    m1 = add_model(client, title="One", files=[("files", ("a.stl", b"one", "model/stl"))])
    m2 = add_model(client, title="Two")
    [fid] = _file_ids(client, m1)
    token = csrf(client, f"/models/{m2}/edit")
    r = client.post(f"/models/{m2}/files/{fid}/delete", data={"csrf_token": token})
    assert r.status_code == 404
    assert _file_ids(client, m1) == [fid]


def test_missing_blob_shows_missing_and_404s(client):
    do_setup(client)
    mid = add_model(client, title="Lost", files=[("files", ("a.stl", b"lost bytes", "model/stl"))])
    [fid] = _file_ids(client, mid)
    from app.services import file_store
    with SessionLocal() as db:
        file_store.blob_path(db.query(StoredFile).first().sha256).unlink()
    page = client.get(f"/models/{mid}").text
    assert "file missing" in page and f"/models/files/{fid}" not in page
    assert client.get(f"/models/files/{fid}").status_code == 404
