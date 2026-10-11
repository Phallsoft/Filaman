import pytest

from app.services.library_urls import is_http_url, platform_for, title_from_url


@pytest.mark.parametrize("url,expected", [
    ("https://www.printables.com/model/123456-wera-bit-holder", "Wera bit holder"),
    ("https://www.printables.com/model/123456-wera-bit-holder/files", "Wera bit holder"),
    ("https://makerworld.com/en/models/987654-articulated-dragon#profileId-1", "Articulated dragon"),
    ("https://cults3d.com/en/3d-model/home/cable_clip_v2", "Cable clip v2"),
    ("https://example.com/downloads/Gear%20Box.stl", "Gear Box"),
    ("https://www.thingiverse.com/thing:4567890", None),
    ("https://example.com/", None),
    ("https://example.com/12345", None),
    ("not a url", None),
])
def test_title_from_url(url, expected):
    assert title_from_url(url) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.printables.com/model/1-x", "Printables"),
    ("https://thingiverse.com/thing:1", "Thingiverse"),
    ("https://makerworld.com/en/models/1", "MakerWorld"),
    ("https://cults3d.com/en/3d-model/x", "Cults"),
    ("https://www.myminifactory.com/object/x", "MyMiniFactory"),
    ("https://thangs.com/x", "Thangs"),
    ("https://grabcad.com/library/x", "GrabCAD"),
    ("https://github.com/a/b", "GitHub"),
    ("https://www.example.org/x", "example.org"),
    ("ftp://example.org/x", None),
    ("", None),
])
def test_platform_for(url, expected):
    assert platform_for(url) == expected


def test_is_http_url():
    assert is_http_url("https://a.b/c") and is_http_url("http://a.b")
    assert not is_http_url("javascript:alert(1)") and not is_http_url("a.b") and not is_http_url("")
