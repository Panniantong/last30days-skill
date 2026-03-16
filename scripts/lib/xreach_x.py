"""X/Twitter search via xreach CLI (Agent Reach free backend).

Uses xreach CLI (https://github.com/Panniantong/xfetch) for Twitter/X search.
No API key needed — uses cookie-based auth configured via agent-reach.
Replaces bird_x.py / xai_x.py / scrapecreators_x.py as a free alternative.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .schema import Engagement, XItem
from .relevance import token_overlap_relevance as _compute_relevance
from .query import extract_core_subject

# Depth configurations
DEPTH_CONFIG = {
    "quick": 10,
    "default": 20,
    "deep": 50,
}


def _log(msg: str):
    sys.stderr.write(f"[xreach] {msg}\n")
    sys.stderr.flush()


def is_xreach_installed() -> bool:
    """Check if xreach CLI is available."""
    return shutil.which("xreach") is not None


def search_x(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search X using xreach CLI.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD) — unused, kept for API compat
        depth: "quick", "default", or "deep"

    Returns:
        Raw xreach JSON response or error dict.
    """
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    timeout = 30 if depth == "quick" else 60

    core_topic = extract_core_subject(topic, max_words=5, strip_suffixes=True)
    query = f"{core_topic} since:{from_date}"

    _log(f"Searching: {query} (n={count})")

    try:
        result = subprocess.run(
            ["xreach", "search", query, "-n", str(count), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            err = result.stderr.strip() if result.stderr else f"exit code {result.returncode}"
            _log(f"xreach error: {err}")
            return {"error": err, "items": []}

        data = json.loads(result.stdout)
        return data

    except subprocess.TimeoutExpired:
        _log(f"xreach timed out after {timeout}s")
        return {"error": "timeout", "items": []}
    except json.JSONDecodeError as e:
        _log(f"xreach JSON parse error: {e}")
        return {"error": f"json_parse: {e}", "items": []}
    except Exception as e:
        _log(f"xreach unexpected error: {e}")
        return {"error": str(e), "items": []}


def search_handles(
    handles: List[str],
    from_date: str,
    count_per: int = 10,
) -> Dict[str, Any]:
    """Search specific X handles using xreach tweets command.

    Args:
        handles: List of handles (without @)
        from_date: Start date (YYYY-MM-DD) — for relevance filtering
        count_per: Number of tweets per handle

    Returns:
        Combined raw response with items from all handles.
    """
    all_items = []
    for handle in handles[:5]:  # Cap at 5 handles
        try:
            result = subprocess.run(
                ["xreach", "tweets", f"@{handle}", "-n", str(count_per), "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                items = data.get("items", data.get("tweets", []))
                all_items.extend(items)
        except Exception as e:
            _log(f"xreach handle search error for @{handle}: {e}")

    return {"items": all_items}


def parse_xreach_response(
    response: Dict[str, Any],
    query: str = "",
) -> List[XItem]:
    """Parse xreach JSON into normalized XItem list.

    Args:
        response: Raw xreach JSON response
        query: Original query for relevance scoring

    Returns:
        List of XItem instances.
    """
    items = response.get("items", [])
    results = []

    for raw in items:
        tweet_id = str(raw.get("id", ""))
        text = raw.get("text", "")

        if not tweet_id or not text:
            continue

        # Skip retweets
        if raw.get("isRetweet", False):
            continue

        # Build URL
        # xreach doesn't always include full user info, construct URL from ID
        url = f"https://x.com/i/status/{tweet_id}"

        # Parse date
        date_str = raw.get("createdAt", "")
        date_iso = None
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
                date_iso = dt.strftime("%Y-%m-%d")
            except ValueError:
                date_iso = date_str[:10] if len(date_str) >= 10 else None

        # Engagement
        engagement = Engagement(
            likes=raw.get("likeCount", 0),
            reposts=raw.get("retweetCount", 0),
            replies=raw.get("replyCount", 0),
            quotes=raw.get("quoteCount", 0),
            views=raw.get("viewCount", 0),
        )

        # Author handle — xreach may not include screen_name directly
        author = raw.get("user", {})
        handle = author.get("screenName", author.get("screen_name", f"id:{author.get('restId', tweet_id)}"))

        # Relevance
        rel = _compute_relevance(text, query) if query else 0.5

        item = XItem(
            id=tweet_id,
            text=text,
            url=url,
            author_handle=handle,
            date=date_iso,
            date_confidence="high" if date_iso else "low",
            engagement=engagement,
            relevance=rel,
            why_relevant=f"matched query: {query}" if rel > 0.3 else "",
        )
        results.append(item)

    return results
