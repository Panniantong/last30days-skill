"""Reddit search via public JSON API (Agent Reach free backend).

Uses Reddit's public JSON endpoints for search and thread retrieval.
No API key needed — just HTTP calls via stdlib urllib.
Replaces reddit.py / openai_reddit.py as a free alternative.

Note: Server IPs may get 403 from Reddit. Falls back gracefully.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .relevance import token_overlap_relevance as _compute_relevance
from .query import extract_core_subject

# Reddit JSON API endpoints
REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
REDDIT_THREAD_URL = "https://www.reddit.com"

USER_AGENT = "agent-reach/1.0 (research-skill; https://github.com/Panniantong/Agent-Reach)"

# Proxy config — load from shared config if available
_PROXY_ENV_FILE = os.path.expanduser("~/.openclaw/shared-config/proxy.env")

# Depth configurations
DEPTH_CONFIG = {
    "quick": 10,
    "default": 25,
    "deep": 50,
}


def _log(msg: str):
    sys.stderr.write(f"[reddit-json] {msg}\n")
    sys.stderr.flush()


def _get_proxy_url() -> Optional[str]:
    """Get proxy URL from env or shared config file."""
    # Check env first
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        return proxy

    # Try loading from shared config (handles export and $VAR references)
    if os.path.exists(_PROXY_ENV_FILE):
        try:
            values = {}
            with open(_PROXY_ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    # Strip 'export '
                    if line.startswith("export "):
                        line = line[7:]
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    # Resolve $VAR references
                    for ref_key, ref_val in values.items():
                        val = val.replace(f"${ref_key}", ref_val)
                    values[key] = val

            return values.get("HTTPS_PROXY") or values.get("HTTP_PROXY") or values.get("PROXY_URL")
        except Exception:
            pass
    return None


def _fetch_json(url: str, timeout: int = 15) -> Optional[Dict]:
    """Fetch JSON from a URL with proper User-Agent, using proxy if available."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })

    # Set up proxy handler
    proxy_url = _get_proxy_url()
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _log(f"HTTP {e.code} from {url}")
        return None
    except urllib.error.URLError as e:
        _log(f"URL error: {e.reason}")
        return None
    except Exception as e:
        _log(f"Fetch error: {e}")
        return None


def search_reddit(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """Search Reddit using public JSON API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD) — used for time filter
        to_date: End date (YYYY-MM-DD)
        depth: "quick", "default", or "deep"
        mock: If True, return empty (for testing)

    Returns:
        Tuple of (items_as_dicts, raw_response, error_string_or_None)
    """
    if mock:
        return [], {}, None

    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = extract_core_subject(topic, max_words=6, strip_suffixes=True)

    _log(f"Searching Reddit: {core_topic} (limit={count})")

    # Build search URL
    params = urllib.parse.urlencode({
        "q": core_topic,
        "limit": min(count, 100),  # Reddit caps at 100
        "t": "month",
        "sort": "relevance",
        "type": "link",
    })
    url = f"{REDDIT_SEARCH_URL}?{params}"

    data = _fetch_json(url, timeout=20)
    if data is None:
        return [], {}, "Reddit API returned no data (possibly blocked by IP)"

    raw_items = data.get("data", {}).get("children", [])
    if not raw_items:
        # Retry with broader time range
        params_retry = urllib.parse.urlencode({
            "q": core_topic,
            "limit": min(count, 100),
            "t": "year",
            "sort": "relevance",
            "type": "link",
        })
        url_retry = f"{REDDIT_SEARCH_URL}?{params_retry}"
        data = _fetch_json(url_retry, timeout=20) or {}
        raw_items = data.get("data", {}).get("children", [])

    items = _parse_reddit_items(raw_items, core_topic)

    # Try to enrich top items with comments
    for item in items[:5]:
        _enrich_with_comments(item)

    return items, data, None


def _parse_reddit_items(
    raw_items: List[Dict],
    query: str,
) -> List[Dict[str, Any]]:
    """Parse Reddit JSON listing into dict list (matching openai_reddit output)."""
    results = []

    for child in raw_items:
        post = child.get("data", {})
        post_id = post.get("id", "")
        title = post.get("title", "")
        subreddit = post.get("subreddit", "")
        permalink = post.get("permalink", "")

        if not post_id or not title:
            continue

        # Skip removed/deleted
        if post.get("removed_by_category") or post.get("selftext") == "[removed]":
            continue

        url = f"https://www.reddit.com{permalink}" if permalink else ""

        # Date
        created_utc = post.get("created_utc", 0)
        date_iso = None
        if created_utc:
            try:
                date_iso = datetime.utcfromtimestamp(created_utc).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        # Relevance
        full_text = f"{title} {post.get('selftext', '')[:500]}"
        rel = _compute_relevance(full_text, query) if query else 0.5

        item = {
            "id": post_id,
            "title": title,
            "url": url,
            "subreddit": subreddit,
            "date": date_iso,
            "date_confidence": "high" if date_iso else "low",
            "engagement": {
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "upvote_ratio": post.get("upvote_ratio"),
            },
            "top_comments": [],
            "comment_insights": [],
            "relevance": rel,
            "why_relevant": f"matched: {query}" if rel > 0.3 else "",
        }
        results.append(item)

    return results


def _enrich_with_comments(item: Dict[str, Any]):
    """Fetch top comments for a Reddit post dict."""
    url = item.get("url", "")
    if not url:
        return

    comments_url = f"{url}.json?limit=5&sort=top"
    data = _fetch_json(comments_url, timeout=10)

    if not data or not isinstance(data, list) or len(data) < 2:
        return

    comments_listing = data[1].get("data", {}).get("children", [])

    for child in comments_listing[:3]:
        comment = child.get("data", {})
        body = comment.get("body", "")
        score = comment.get("score", 0)
        author = comment.get("author", "")

        if not body or author in ("[deleted]", "AutoModerator"):
            continue
        if len(body) < 10:
            continue

        item["top_comments"].append({
            "excerpt": body[:500],
            "author": author,
            "score": score,
            "date": None,
            "url": f"https://www.reddit.com{comment.get('permalink', '')}",
        })
