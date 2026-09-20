import base64
import binascii
import os
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import config
from .ai_import import ImportError_, MAX_REDIRECTS, _assert_public_host

MAX_IMAGE_BYTES = 8 * 1024 * 1024
OUTPUT_SIZE = 720
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ImageError(Exception):
    """User-facing image failure."""


def media_root() -> Path:
    root = Path(config.MEDIA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    (root / "spools").mkdir(parents=True, exist_ok=True)
    return root


def delete_image(image_path: str | None) -> None:
    if not image_path:
        return
    root = media_root().resolve()
    path = (root / image_path).resolve()
    if root in path.parents and path.exists():
        path.unlink()


def fetch_remote_image(url: str) -> tuple[bytes, str]:
    url = (url or "").strip()
    if urlparse(url).scheme not in ("http", "https"):
        raise ImageError("Image URL must be http(s).")
    try:
        _assert_public_host(url)
        with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (compatible; Filaman/1.0)"}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                _assert_public_host(url)
                resp = client.get(url)
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    url = urljoin(url, resp.headers["location"])
                    continue
                resp.raise_for_status()
                break
            else:
                raise ImageError("Too many image redirects.")
    except ImportError_ as e:
        raise ImageError(str(e))
    except httpx.HTTPStatusError as e:
        raise ImageError(f"Image URL returned HTTP {e.response.status_code}.")
    except httpx.HTTPError as e:
        raise ImageError(f"Could not fetch image URL: {e}")

    content_type = resp.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ImageError("Image URL did not return a supported image type.")
    content = resp.content[: MAX_IMAGE_BYTES + 1]
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageError("Image is too large; use an image under 8 MB.")
    return content, content_type or "image/jpeg"


async def save_spool_image(
    *,
    upload: UploadFile | None = None,
    source_url: str | None = None,
    cropped_image: str | None = None,
    replace_path: str | None = None,
) -> str | None:
    raw: bytes | None = None
    if cropped_image:
        raw = _decode_data_url(cropped_image)
    elif upload and upload.filename:
        raw = await upload.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImageError("Image is too large; use an image under 8 MB.")
    elif source_url:
        raw, _ = fetch_remote_image(source_url)

    if raw is None:
        return None

    image_path = _write_normalized_image(raw, force_square=bool(cropped_image))
    if replace_path and replace_path != image_path:
        delete_image(replace_path)
    return image_path


def _decode_data_url(value: str) -> bytes:
    header, sep, payload = value.partition(",")
    if not sep or not header.startswith("data:image/") or ";base64" not in header:
        raise ImageError("Cropped image data was invalid.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ImageError("Cropped image data was invalid.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError("Image is too large; use an image under 8 MB.")
    return raw


def _write_normalized_image(raw: bytes, force_square: bool = False) -> str:
    try:
        with Image.open(BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            canvas = Image.new("RGB", (OUTPUT_SIZE, OUTPUT_SIZE), "white")
            if force_square:
                img = img.resize((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
            else:
                img.thumbnail((OUTPUT_SIZE, OUTPUT_SIZE), Image.Resampling.LANCZOS)
            x = (OUTPUT_SIZE - img.width) // 2
            y = (OUTPUT_SIZE - img.height) // 2
            if img.mode in ("RGBA", "LA"):
                canvas.paste(img, (x, y), img.getchannel("A"))
            else:
                canvas.paste(img.convert("RGB"), (x, y))
    except (UnidentifiedImageError, OSError):
        raise ImageError("That file could not be read as an image.")

    rel_path = f"spools/{uuid.uuid4().hex}.jpg"
    path = media_root() / rel_path
    tmp_path = path.with_suffix(".tmp")
    canvas.save(tmp_path, format="JPEG", quality=88, optimize=True)
    os.replace(tmp_path, path)
    return rel_path