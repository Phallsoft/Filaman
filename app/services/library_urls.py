"""Offline helpers for model URLs: a title guess and a platform name, no network."""
import re
from urllib.parse import unquote, urlparse

PLATFORMS = {
    "printables.com": "Printables",
    "thingiverse.com": "Thingiverse",
    "makerworld.com": "MakerWorld",
    "cults3d.com": "Cults",
    "myminifactory.com": "MyMiniFactory",
    "thangs.com": "Thangs",
    "grabcad.com": "GrabCAD",
    "github.com": "GitHub",
}

# Path segments that describe the site's URL scheme rather than the model.
GENERIC_SEGMENTS = {
    "model", "models", "3d-model", "3d-models", "thing", "things", "files", "file",
    "download", "downloads", "object", "objects", "library", "design", "designs",
    "home", "en", "de", "fr", "es", "it", "nl", "pl", "ja", "zh",
}

_EXT_RE = re.compile(r"\.(stl|3mf|obj|step|stp|zip|gcode|f3d|html?)$", re.IGNORECASE)
_LEADING_ID_RE = re.compile(r"^\d+[-_]+")
_ID_REF_RE = re.compile(r"^[a-z]+:\d+$", re.IGNORECASE)  # thingiverse "thing:123"


def is_http_url(url: str | None) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def platform_for(url: str | None) -> str | None:
    if not is_http_url(url):
        return None
    host = (urlparse(url.strip()).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for domain, name in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or None


def _segment_title(segment: str) -> str | None:
    text = unquote(segment).strip()
    if not text or text.lower() in GENERIC_SEGMENTS or _ID_REF_RE.match(text):
        return None
    text = _EXT_RE.sub("", text)
    text = _LEADING_ID_RE.sub("", text)
    text = re.sub(r"[-_+]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not re.search(r"[^\W\d_]", text):  # needs at least one letter
        return None
    return text[0].upper() + text[1:]


def title_from_url(url: str | None) -> str | None:
    """Guess a title from the URL path: '/model/123-wera-bit-holder' -> 'Wera bit holder'."""
    if not is_http_url(url):
        return None
    segments = [s for s in urlparse(url.strip()).path.split("/") if s]
    for segment in reversed(segments):
        title = _segment_title(segment)
        if title:
            return title[:255]
    return None
