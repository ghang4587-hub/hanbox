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
from functools import lru_cache
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
    value = re.sub(r"#\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def parse_views(entry: ET.Element) -> int:
    node = next(entry.iter("{%s}statistics" % ATOM_NS["media"]), None)
    try:
        return int((node.attrib.get("views") if node is not None else "0") or 0)
    except (TypeError, ValueError):
        return 0


def synopsis_from_description(description: str, title: str, limit: int = 150) -> str:
    clean = clean_text(description, 340)
    match = re.search(r"(?:synopsis|story|plot|introduction|简介|故事梗概)\s*:\s*(.+)", clean, re.I)
    result = match.group(1) if match else clean
    return (result or title)[:limit].rstrip(" .")


@lru_cache(maxsize=256)
def translate_to_zh(text: str) -> str:
    """Translate a public English synopsis to Simplified Chinese for the card."""
    source = re.sub(r"\s+", " ", text or "").strip()
    if not source or re.search(r"[\u3400-\u9fff]", source):
        return source
    query = urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": source}
    )
    try:
        payload = json.loads(fetch_bytes(f"https://translate.googleapis.com/translate_a/single?{query}"))
        translated = "".join(part[0] for part in payload[0] if part and part[0])
        return re.sub(r"\s+", " ", translated).strip()
    except Exception:
        return ""


def direct_card_copy(title: str, synopsis: str) -> dict[str, str]:
    """Create plain Chinese card copy without exposing raw feed boilerplate."""
    text = f"{title} {synopsis}".lower()
    rules = [
        (
            ("auction", "sold"),
            "主角被绑架后送上地下拍卖台，买下她的人却和她以为已经死去的爱人有关。她必须先逃出去，再查清对方的真实身份。",
            "画面：主角戴着锁链被推上拍卖台，神秘买家高价拍下她。｜吸睛点：人身危机和身份悬念同时出现。",
        ),
        (
            ("cheat", "betray", "lover", "mistress", "divorce", "ex "),
            "主角发现最亲近的人背叛了自己。她不再继续忍耐，而是离开旧关系并准备让背叛者付出代价。",
            "画面：主角当场撞破背叛，对方还以为她会继续忍。｜吸睛点：先把委屈压到最低，观众会马上等她反击。",
        ),
        (
            ("wedding", "bride", "fiancé", "fiance", "altar"),
            "婚礼现场突然失控，主角被背叛、替嫁或当众抢走。她必须在众目睽睽下做出选择，并查清这场婚礼背后的算计。",
            "画面：婚礼刚开始就有人闯入或揭穿秘密，所有宾客同时看向主角。｜吸睛点：公开场合翻车，羞辱和反转一眼就懂。",
        ),
        (
            ("baby", "daughter", "son", "mom", "mother", "pregnant"),
            "孩子或母亲的身份被人隐瞒，主角因此失去了最重要的家人。她开始追查真相，也逼迫伤害家人的人面对后果。",
            "画面：一个胎记、孕检结果或孩子的称呼突然暴露关系。｜吸睛点：认亲信息一出现，心疼和悬念会同时拉满。",
        ),
        (
            ("billionaire", "mafia", "king", "queen", "boss", "heir"),
            "主角原本被当成普通人或牺牲品，随后却被真正有权势的人选中。隐藏身份曝光后，原先欺负她的人开始后悔。",
            "画面：主角刚被看不起，真正掌权的人就走到她身边并公开护住她。｜吸睛点：地位在十秒内翻转，爽点非常直接。",
        ),
        (
            ("apocalypse", "system", "starve", "food", "zombie"),
            "末日中所有人都在争抢资源，主角却突然得到系统或无限物资。他利用这个优势活下来，并建立自己的势力。",
            "画面：别人正为一口食物拼命，主角面前却出现大量资源或系统奖励。｜吸睛点：极端资源差不用解释就能看懂。",
        ),
        (
            ("professor", "school", "college", "student", "class"),
            "主角在校园里遇到一段不能公开的关系。两人越想装作陌生，过去的秘密越容易被其他人发现。",
            "画面：两人在课堂重新见面并立刻认出对方，却必须假装从未认识。｜吸睛点：观众先知道秘密，会一直等它被戳破。",
        ),
        (
            ("secret", "hidden", "identity", "dragon rider", "superhero"),
            "所有人都看错了主角的身份。危机出现后，主角露出真正能力，并开始清算曾经羞辱自己的人。",
            "画面：主角先被当成弱者，下一秒直接亮出隐藏能力。｜吸睛点：外表和实力反差越大，打脸越快。",
        ),
    ]
    for words, story, first_ten in rules:
        if any(word in text for word in words):
            return {"story": story, "ten": first_ten}
    return {
        "story": "主角一开场就被卷入一场突发冲突。为了摆脱眼前困境，主角必须马上做出选择，并找出幕后真正的操控者。",
        "ten": "画面：主角的目标刚出现，眼前的阻碍就立刻打断计划。｜吸睛点：人物要什么、谁在阻止，十秒内交代清楚。",
    }


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
        story_source = synopsis_from_description(description, title, limit=260)
        card_copy = direct_card_copy(title, hook)
        translated_hook = translate_to_zh(hook)
        display_hook = translated_hook or hook
        translated_story = translate_to_zh(story_source)
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
                "h": display_hook,
                "hEn": hook,
                "ten": card_copy["ten"],
                "story": translated_story or card_copy["story"],
                "u": "先验证前 180 秒的身份、羞辱或反击节点，再决定是否拆成买量素材。",
                "m": generic_moments(display_hook),
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
    for previous in existing.get("videos", []):
        card_copy = direct_card_copy(str(previous.get("t", "")), str(previous.get("h", "")))
        previous.setdefault("ten", card_copy["ten"])
        previous_hook = str(previous.get("h", ""))
        if previous_hook and not re.search(r"[\u3400-\u9fff]", previous_hook):
            translated_hook = translate_to_zh(previous_hook)
            if translated_hook:
                previous["h"] = translated_hook
                if isinstance(previous.get("m"), list) and previous["m"] and isinstance(previous["m"][0], list):
                    previous["m"][0][1] = translated_hook
        story_source = synopsis_from_description(
            previous_hook, str(previous.get("t", "")), limit=260
        )
        existing_story = str(previous.get("story", ""))
        if not existing_story or not re.search(r"[\u3400-\u9fff]", existing_story):
            translated_story = translate_to_zh(story_source)
            previous["story"] = translated_story or existing_story or card_copy["story"]
    by_id = {item.get("id"): item for item in existing.get("videos", []) if item.get("id")}
    for item in fetched:
        previous = by_id.get(item["id"])
        if previous:
            # Keep any manually edited analysis while refreshing factual fields.
            if previous.get("source") == "youtube-atom":
                for key in ("s", "g", "h", "hEn", "ten", "story", "u", "m"):
                    previous[key] = item[key]
            elif item.get("story"):
                previous["story"] = item["story"]
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


