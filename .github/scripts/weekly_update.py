#!/usr/bin/env python3
"""Refresh the small weekly YouTube feed used by the static GitHub Pages site.

The public Atom feed is the no-key fallback.  When YOUTUBE_API_KEY is present,
the script enriches the same records with the Data API's exact duration and
view count.  It intentionally writes JSON only; the HTML remains a static,
cacheable artifact and keeps its built-in sample data as a fallback.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "youtube-channels.json"
DATA_PATH = ROOT / "data" / "weekly.json"
USER_AGENT = "MergeSparkRadarWeeklyRefresh/1.0 (+GitHub Actions)"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read()


def text_at(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    child = node.find(path, ATOM_NS)
    return (child.text or "").strip() if child is not None else ""


def clean_text(value: str, limit: int = 260) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def parse_views(entry: ET.Element) -> int:
    node = next(entry.iter("{%s}statistics" % ATOM_NS["media"]), None)
    try:
        return int((node.attrib.get("views") if node is not None else "0") or 0)
    except (TypeError, ValueError):
        return 0


def synopsis_from_description(description: str, title: str) -> str:
    clean = clean_text(description, 340)
    match = re.search(r"(?:synopsis|story|plot|简介|故事梗概)\s*:\s*(.+)", clean, re.I)
    result = match.group(1) if match else clean
    return (result or title)[:150].rstrip(" .")


def tags_for(title: str, description: str) -> list[str]:
    haystack = f"{title} {description}".lower()
    rules = [
        ("复仇", ("revenge", "betray", "cheat", "divorce", "regret", "ex-")),
        ("豪门", ("billionaire", "mafia", "boss", "rich", "heiress", "king")),
        ("身份反转", ("secret", "identity", "hidden", "mistaken", "revealed")),
        ("亲情", ("mother", "mom", "baby", "daughter", "son", "family")),
        ("校园", ("school", "professor", "college", "class")),
        ("末日", ("apocalypse", "survive", "starve", "zombie")),
    ]
    tags = [label for label, words in rules if any(word in haystack for word in words)]
    if not tags:
        tags = ["新样本", "冲突开场"]
    for fallback in ("冲突开场", "YouTube"):
        if len(tags) >= 3:
            break
        if fallback not in tags:
            tags.append(fallback)
    return tags[:3]


def hook_score(title: str, synopsis: str) -> int:
    haystack = f"{title} {synopsis}".lower()
    score = 78
    score += 5 * sum(
        marker in haystack
        for marker in ("billionaire", "mafia", "revenge", "secret", "cheat", "dead", "wedding")
    )
    return max(72, min(96, score))


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def generic_moments(hook: str) -> list[list[str]]:
    return [
        ["00:00", hook],
        ["00:58", "冲突升级，隐藏关系或真实目的被揭开。"],
        ["01:46", "对立双方被迫正面交锋，情绪开始翻面。"],
        ["02:43", "新线索出现，留下下一集悬念。"],
    ]


def feed_entries(channel: dict) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['id']}"
    root = ET.fromstring(fetch_bytes(url))
    result: list[dict] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        video_id = text_at(entry, "yt:videoId")
        title = clean_text(text_at(entry, "atom:title"), 180)
        published = text_at(entry, "atom:published")
        description = ""
        for node in entry.iter("{%s}description" % ATOM_NS["media"]):
            description = node.text or ""
            break
        hook = synopsis_from_description(description, title)
        if not video_id or not title:
            continue
        result.append(
            {
                "id": video_id,
                "t": title,
                "c": channel["label"],
                "d": 0,
                "s": hook_score(title, hook),
                "g": tags_for(title, description),
                "h": hook,
                "u": "先验证前 180 秒的身份、羞辱或反击节点，再决定是否拆成买量素材。",
                "m": generic_moments(hook),
                "v": parse_views(entry),
                "p": published,
                "source": "youtube-atom",
                "sourceUrl": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    return result


def api_enrich(items: list[dict], api_key: str) -> None:
    if not items or not api_key:
        return
    ids = [item["id"] for item in items]
    for start in range(0, len(ids), 50):
        query = urllib.parse.urlencode(
            {"part": "contentDetails,statistics", "id": ",".join(ids[start : start + 50]), "key": api_key}
        )
        payload = json.loads(fetch_bytes(f"https://www.googleapis.com/youtube/v3/videos?{query}"))
        by_id = {item.get("id"): item for item in payload.get("items", [])}
        for item in items[start : start + 50]:
            api_item = by_id.get(item["id"], {})
            item["d"] = parse_duration(api_item.get("contentDetails", {}).get("duration", ""))
            try:
                item["v"] = int(api_item.get("statistics", {}).get("viewCount", item.get("v", 0)))
            except (TypeError, ValueError):
                pass
            item["source"] = "youtube-data-api"


def load_existing() -> dict:
    if not DATA_PATH.exists():
        return {"version": 1, "generatedAt": None, "source": "YouTube public channel feeds", "videos": []}
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        payload["videos"] = payload.get("videos") if isinstance(payload.get("videos"), list) else []
        return payload
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "generatedAt": None, "source": "YouTube public channel feeds", "videos": []}


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    channels = [channel for channel in config.get("channels", []) if channel.get("id")]
    if not channels:
        print("No YouTube channels configured", file=sys.stderr)
        return 2

    fetched: list[dict] = []
    for channel in channels:
        try:
            entries = feed_entries(channel)
            print(f"{channel['label']}: {len(entries)} feed entries")
            fetched.extend(entries)
        except Exception as exc:  # one channel should not block the other
            print(f"warning: failed to fetch {channel['label']}: {exc}", file=sys.stderr)

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if api_key:
        try:
            api_enrich(fetched, api_key)
            print("enriched durations and view counts with YouTube Data API")
        except Exception as exc:
            print(f"warning: API enrichment failed; keeping RSS values: {exc}", file=sys.stderr)

    existing = load_existing()
    by_id = {item.get("id"): item for item in existing.get("videos", []) if item.get("id")}
    for item in fetched:
        previous = by_id.get(item["id"])
        if previous:
            # Keep any manually edited analysis while refreshing factual fields.
            if previous.get("source") == "youtube-atom":
                for key in ("s", "g", "h", "u", "m"):
                    previous[key] = item[key]
            for key in ("t", "c", "d", "v", "p", "source", "sourceUrl"):
                if item.get(key) not in (None, "", 0) or key in ("t", "c", "p", "source", "sourceUrl"):
                    previous[key] = item.get(key)
        else:
            by_id[item["id"]] = item

    videos = list(by_id.values())
    videos.sort(key=lambda item: item.get("p") or "", reverse=True)
    existing.update(
        {
            "version": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": "YouTube public channel feeds" + (" + Data API" if api_key else ""),
            "videos": videos[:90],
        }
    )
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(existing['videos'])} weekly records to {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

