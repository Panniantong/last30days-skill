"""Web search via Exa MCP (Agent Reach free backend).

Uses Exa's free web search through mcporter MCP integration.
No API key needed — Exa provides free tier via MCP.
Replaces brave_search.py / parallel_search.py / openrouter_search.py.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .schema import WebSearchItem
from .relevance import token_overlap_relevance as _compute_relevance
from .query import extract_core_subject

# Depth configurations
DEPTH_CONFIG = {
    "quick": 5,
    "default": 10,
    "deep": 20,
}


def _log(msg: str):
    sys.stderr.write(f"[exa] {msg}\n")
    sys.stderr.flush()


def is_exa_installed() -> bool:
    """Check if mcporter CLI is available for Exa MCP calls."""
    return shutil.which("mcporter") is not None


def _extract_domain(url: str) -> str:
    """Extract readable domain from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Strip www.
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "web"


def search_web(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> List[WebSearchItem]:
    """Search the web using Exa via mcporter.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: "quick", "default", or "deep"

    Returns:
        List of WebSearchItem.
    """
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = extract_core_subject(topic, max_words=8, strip_suffixes=False)

    _log(f"Searching Exa: {core_topic} (n={count})")

    # Escape quotes in query for the mcporter call
    safe_query = core_topic.replace('"', '\\"')

    # Find mcporter config — check common locations
    config_path = None
    for candidate in [
        os.path.expanduser("~/.openclaw/shared-config/mcporter.json"),
        os.path.expanduser("~/.mcporter/mcporter.json"),
        os.path.join(os.getcwd(), "config", "mcporter.json"),
    ]:
        if os.path.exists(candidate):
            config_path = candidate
            break

    cmd = ["mcporter"]
    if config_path:
        cmd.extend(["--config", config_path])
    cmd.extend(["call", f'exa.web_search_exa(query: "{safe_query}", numResults: {count})'])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            err = result.stderr.strip() if result.stderr else f"exit {result.returncode}"
            _log(f"mcporter error: {err}")
            return []

        return _parse_exa_output(result.stdout, core_topic)

    except subprocess.TimeoutExpired:
        _log("Exa search timed out")
        return []
    except Exception as e:
        _log(f"Exa error: {e}")
        return []


def _parse_exa_output(output: str, query: str) -> List[WebSearchItem]:
    """Parse mcporter/Exa output into WebSearchItem list.

    Exa MCP returns text output with results. Format varies but typically:
    - Title, URL, snippet blocks
    - Or JSON array
    """
    items = []

    # Try JSON parse first
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            results = data.get("results", data.get("items", []))
        elif isinstance(data, list):
            results = data
        else:
            results = []

        for i, r in enumerate(results):
            url = r.get("url", "")
            title = r.get("title", "")
            snippet = r.get("text", r.get("snippet", r.get("highlights", "")))
            if isinstance(snippet, list):
                snippet = " ".join(str(s) for s in snippet[:3])

            date = r.get("publishedDate", r.get("date", ""))
            if date and len(date) >= 10:
                date = date[:10]
            else:
                date = None

            domain = _extract_domain(url) if url else "web"
            rel = _compute_relevance(f"{title} {snippet}", query) if query else 0.5

            items.append(WebSearchItem(
                id=f"exa_{i}",
                title=title or url,
                url=url,
                source_domain=domain,
                snippet=str(snippet)[:500] if snippet else "",
                date=date,
                date_confidence="medium" if date else "low",
                relevance=rel,
                why_relevant=f"exa search: {query}" if rel > 0.3 else "",
            ))

        if items:
            return items

    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: parse text output
    # mcporter output is typically plain text with URLs
    url_pattern = re.compile(r'https?://\S+')
    urls_found = url_pattern.findall(output)

    # Extract blocks around each URL
    lines = output.split('\n')
    for i, line in enumerate(lines):
        urls_in_line = url_pattern.findall(line)
        for url in urls_in_line:
            # Skip Reddit/X (covered by other sources)
            domain = _extract_domain(url)
            if domain in ("reddit.com", "x.com", "twitter.com", "youtube.com"):
                continue

            # Use surrounding lines as title/snippet
            title = lines[i - 1].strip() if i > 0 else ""
            snippet_line = lines[i + 1].strip() if i + 1 < len(lines) else ""

            # Clean up title
            title = title.lstrip("0123456789.-) ")
            if not title:
                title = domain

            rel = _compute_relevance(f"{title} {snippet_line}", query) if query else 0.5

            items.append(WebSearchItem(
                id=f"exa_text_{len(items)}",
                title=title,
                url=url,
                source_domain=domain,
                snippet=snippet_line[:500],
                date=None,
                date_confidence="low",
                relevance=rel,
                why_relevant=f"exa search: {query}" if rel > 0.3 else "",
            ))

    return items
