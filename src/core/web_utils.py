"""Web utilities: fetch URLs, search the web.

Stdlib-only implementation (urllib, html.parser) — zero runtime dependencies.
Optional: can use aiohttp/httpx if available for async support.
"""

import asyncio
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


class _HTMLToTextParser(HTMLParser):
    """Convert HTML to plain text using stdlib HTMLParser."""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._ignore_tags = {"script", "style", "noscript", "iframe", "svg", "canvas"}
        self._ignore_depth = 0
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._ignore_tags:
            self._ignore_depth += 1
        elif tag_lower == "body":
            self._in_body = True
        elif tag_lower in {
            "p",
            "div",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "tr",
        }:
            self._text_parts.append("\n")
        elif tag_lower == "td":
            self._text_parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._ignore_tags:
            self._ignore_depth = max(0, self._ignore_depth - 1)
        elif tag_lower in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0 and self._in_body:
            cleaned = html.unescape(data)
            cleaned = re.sub(r"\s+", " ", cleaned)
            if cleaned.strip():
                self._text_parts.append(cleaned)

    def get_text(self) -> str:
        text = "".join(self._text_parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html_content: str) -> str:
    """Convert HTML to plain text (stdlib only)."""
    parser = _HTMLToTextParser()
    try:
        parser.feed(html_content)
    except Exception:
        pass
    return parser.get_text()


def is_url(text: str) -> bool:
    """Check if a string is a URL."""
    try:
        result = urlparse(text)
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def fetch_url(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Fetch a URL and return (content, content_type).

    Returns plain text content (HTML converted to text) and the detected content type.
    Raises urllib.error.URLError on failure.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AgentCouncil/1.0; +https://github.com/agentcouncil)"
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        raw_content = response.read()

    # Decode content
    charset = "utf-8"
    if "charset=" in content_type:
        try:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        except Exception:
            pass

    try:
        text_content = raw_content.decode(charset, errors="replace")
    except Exception:
        text_content = raw_content.decode("utf-8", errors="replace")

    # Convert HTML to text if needed
    if "text/html" in content_type:
        text_content = html_to_text(text_content)
        content_type = "text/plain"

    return text_content, content_type


def save_fetched_evidence(
    session_evidence_dir: Path,
    url: str,
    content: str,
    content_type: str,
) -> Path:
    """Save fetched content as evidence file.

    Returns the path to the saved file.
    """
    session_evidence_dir.mkdir(parents=True, exist_ok=True)

    # Generate a safe filename from the URL
    parsed = urlparse(url)
    path_part = parsed.path.strip("/")
    if path_part:
        # Take last path segment, remove extension
        name = path_part.split("/")[-1]
        name = re.sub(r"\.[a-z0-9]+$", "", name, flags=re.IGNORECASE)
    else:
        name = parsed.netloc.replace(".", "_")

    # Sanitize
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:100]
    if not name:
        name = "fetched_page"

    # Add extension based on content type
    if "html" in content_type:
        ext = ".md"  # Save as markdown for readability
    elif "json" in content_type:
        ext = ".json"
    elif "xml" in content_type:
        ext = ".xml"
    else:
        ext = ".txt"

    # Handle collisions
    base_name = name
    counter = 1
    target = session_evidence_dir / f"{name}{ext}"
    while target.exists():
        counter += 1
        target = session_evidence_dir / f"{base_name}_{counter}{ext}"

    target.write_text(content, encoding="utf-8")
    return target


# --- Web Search (DuckDuckGo HTML scrape, stdlib only) ---

_DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"


class _DDGResultParser(HTMLParser):
    """Parse DuckDuckGo HTML results page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._in_result = False
        self._in_title = False
        self._in_snippet = False
        self._current: dict = {}
        self._data_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("class") == "result__snippet":
            self._in_snippet = True
            self._data_buffer = []
        elif tag == "a" and attrs_dict.get("class") == "result__url":
            self._in_title = True
            self._data_buffer = []
            self._current["url"] = attrs_dict.get("href", "")
        elif tag == "div" and attrs_dict.get("class") == "result__title":
            self._in_result = True
            self._current = {"title": "", "snippet": "", "url": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_snippet:
            self._in_snippet = False
            self._current["snippet"] = " ".join(self._data_buffer).strip()
            self._data_buffer = []
        elif tag == "a" and self._in_title:
            self._in_title = False
            self._current["title"] = " ".join(self._data_buffer).strip()
            self._data_buffer = []
        elif tag == "div" and self._in_result:
            self._in_result = False
            if self._current.get("title") or self._current.get("snippet"):
                self.results.append(self._current)

    def handle_data(self, data: str) -> None:
        if self._in_snippet or self._in_title:
            cleaned = html.unescape(data).strip()
            if cleaned:
                self._data_buffer.append(cleaned)


def search_web(query: str, max_results: int = 5, timeout: float = 30.0) -> list[dict]:
    """Search the web using DuckDuckGo (HTML scrape, stdlib only).

    Returns list of dicts: {"title": str, "snippet": str, "url": str}
    """
    params = urllib.parse.urlencode({"q": query, "kl": "us-en"})
    url = f"{_DUCKDUCKGO_HTML_URL}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AgentCouncil/1.0; +https://github.com/agentcouncil)"
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        html_content = response.read().decode("utf-8", errors="replace")

    parser = _DDGResultParser()
    parser.feed(html_content)

    return parser.results[:max_results]


async def search_web_async(
    query: str, max_results: int = 5, timeout: float = 30.0
) -> list[dict]:
    """Async version of search_web using stdlib (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, search_web, query, max_results, timeout)


def format_search_results_for_prompt(results: list[dict]) -> str:
    """Format search results as a markdown block for inclusion in prompts."""
    if not results:
        return "No search results found."

    lines = ["## Web Search Results", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        lines.append(f"### {i}. {title}")
        if snippet:
            lines.append(f"{snippet}")
        if url:
            lines.append(f"Source: {url}")
        lines.append("")
    return "\n".join(lines)
