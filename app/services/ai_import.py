import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .. import config

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 15000


class ImportError_(Exception):
    """User-facing import failure."""


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImportError_("Only http(s) URLs are supported.")
    host = parsed.hostname
    if not host:
        raise ImportError_("Invalid URL.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ImportError_(f"Could not resolve host '{host}'.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImportError_("URL resolves to a private/internal address and was blocked.")


def fetch_page_text(url: str) -> tuple[str, bool]:
    """Fetch a URL's visible text directly, falling back to Bright Data MCP if that fails.

    Returns (text, used_brightdata).
    """
    if urlparse(url).scheme not in ("http", "https"):
        raise ImportError_("Only http(s) URLs are supported.")
    try:
        return _fetch_direct(url), False
    except ImportError_:
        if config.BRIGHTDATA_MCP_URL:
            return fetch_via_brightdata(url), True
        raise


def _parse_mcp_response(resp: httpx.Response) -> dict:
    """Parse a streamable-HTTP MCP response (plain JSON or SSE)."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        message = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    candidate = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                    message = candidate
        if message is None:
            raise ImportError_("Empty response from Bright Data MCP server.")
        return message
    try:
        return resp.json()
    except ValueError:
        raise ImportError_("Invalid response from Bright Data MCP server.")


def fetch_via_brightdata(url: str) -> str:
    """Fetch page content as markdown through the Bright Data MCP server."""
    mcp_url = config.BRIGHTDATA_MCP_URL
    headers = {"Accept": "application/json, text/event-stream"}
    try:
        with httpx.Client(timeout=90, headers=headers) as client:
            resp = client.post(mcp_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "filaman", "version": "1.0"},
                },
            })
            if resp.status_code >= 400:
                raise ImportError_(f"Bright Data MCP init failed: HTTP {resp.status_code}.")
            init_msg = _parse_mcp_response(resp)
            if "error" in init_msg:
                raise ImportError_(f"Bright Data MCP init error: {init_msg['error'].get('message', 'unknown')}.")
            session_id = resp.headers.get("mcp-session-id")
            if session_id:
                client.headers["mcp-session-id"] = session_id
            client.post(mcp_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

            resp = client.post(mcp_url, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "scrape_as_markdown", "arguments": {"url": url}},
            })
            if resp.status_code >= 400:
                raise ImportError_(f"Bright Data MCP call failed: HTTP {resp.status_code}.")
            msg = _parse_mcp_response(resp)
    except httpx.HTTPError as e:
        raise ImportError_(f"Could not reach Bright Data MCP server: {e}")

    if "error" in msg:
        raise ImportError_(f"Bright Data MCP error: {msg['error'].get('message', 'unknown')}.")
    result = msg.get("result") or {}
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    text = "\n".join(t for t in texts if t).strip()
    if result.get("isError"):
        raise ImportError_(f"Bright Data could not scrape that URL: {text[:200] or 'unknown error'}")
    if not text:
        raise ImportError_("Bright Data returned no content for that URL.")
    return text[:MAX_TEXT_CHARS]


def _fetch_direct(url: str) -> str:
    """Fetch a URL directly with SSRF guards and return its visible text."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Filaman/1.0)"}
    with httpx.Client(timeout=15, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_host(url)
            try:
                resp = client.get(url)
            except httpx.HTTPError as e:
                raise ImportError_(f"Failed to fetch URL: {e}")
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code >= 400:
                raise ImportError_(f"Page returned HTTP {resp.status_code}.")
            break
        else:
            raise ImportError_("Too many redirects.")

    content = resp.content[:MAX_BYTES]
    parser = _TextExtractor()
    try:
        parser.feed(content.decode(resp.encoding or "utf-8", errors="replace"))
    except Exception:
        raise ImportError_("Could not parse page content.")
    text = "\n".join(parser.chunks)
    if not text.strip():
        raise ImportError_("Page contained no readable text.")
    return text[:MAX_TEXT_CHARS]


def has_extracted_anything(fields: dict) -> bool:
    return bool(fields.get("manufacturer") or fields.get("material") or fields.get("color_name"))


_PROMPT = """You extract 3D printer filament spool product details from web page text.
Respond with ONLY a JSON object (no markdown, no commentary) with these keys:
- manufacturer: brand/manufacturer name (string or null)
- material: material type such as PLA, PETG, ABS, TPU, ASA, PLA+ (string or null)
- color_name: color name (string or null)
- color_hex: best-guess HTML hex code for the color like "#1A2B3C" (string or null)
- sku: product SKU/model number if present (string or null)
- weight_g: net filament weight in grams as an integer (e.g. 1kg -> 1000) (number or null)
- qty: quantity of spools if the text describes a multi-pack, else 1 (number)
"""


def extract_fields(text: str) -> dict:
    """Call an OpenAI-compatible API to extract spool fields from text."""
    if not config.AI_API_KEY:
        raise ImportError_("No AI API key configured. Set AI_API_KEY in your environment/.env.")
    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": text[:MAX_TEXT_CHARS]},
        ],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {config.AI_API_KEY}"}
    try:
        resp = httpx.post(
            f"{config.AI_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        raise ImportError_(f"AI API error: HTTP {e.response.status_code}.")
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise ImportError_(f"AI API call failed: {e}")

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ImportError_("AI response did not contain JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise ImportError_("AI response contained invalid JSON.")

    weight = data.get("weight_g")
    qty = data.get("qty")
    color_hex = data.get("color_hex") or ""
    return {
        "manufacturer": (data.get("manufacturer") or "").strip() or None,
        "material": (data.get("material") or "").strip() or None,
        "color_name": (data.get("color_name") or "").strip() or None,
        "color_hex": color_hex if re.match(r"^#[0-9a-fA-F]{6}$", str(color_hex)) else None,
        "sku": (str(data.get("sku") or "")).strip() or None,
        "weight_g": int(weight) if isinstance(weight, (int, float)) and weight > 0 else None,
        "qty": int(qty) if isinstance(qty, (int, float)) and qty > 0 else 1,
    }
