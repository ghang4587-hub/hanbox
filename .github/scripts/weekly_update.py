#!/usr/bin/env python3
"""Refresh the small weekly YouTube feed used by the static GitHub Pages site.

The public Atom feed is the no-key fallback.  When YOUTUBE_API_KEY is present,
the script enriches the same records with the Data API's exact duration and
view count.  It also stores ten real storyboard frames for retained videos;
the HTML remains a static, cacheable artifact with built-in fallback data.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "youtube-channels.json"
DATA_PATH = ROOT / "data" / "weekly.json"
FRAMES_ROOT = ROOT / "assets" / "frames"
USER_AGENT = "MergeSparkRadarWeeklyRefresh/1.0 (+GitHub Actions)"
BROWSER_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
SAMPLE_LIMIT = 48
MARKET_SAMPLE_LIMIT = 24
WEEKLY_NEW_LIMIT_PER_MARKET = 6
WEEKLY_FOCUS_LIMIT_PER_MARKET = 8
MEDIA_ENRICH_LIMIT = 12
PLATFORM_SAMPLE_QUOTAS = {
    "overseas": {"DramaBox": 12, "GoodShort": 12},
    "domestic": {"腾讯视频": 12, "优酷": 6, "阅文短剧": 6},
}
FRAME_SECONDS = (0, 20, 40, 58, 80, 100, 106, 130, 145, 163)
INNERTUBE_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read()


def fetch_browser_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": BROWSER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    )
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
    match = re.search(
        r"(?:synopsis|story|plot|introduction|简介|簡介|故事梗概|剧情简介|劇情簡介)\s*[:：]\s*(.+)",
        clean,
        re.I,
    )
    result = match.group(1) if match else clean
    boilerplate_markers = (
        "download \"wetv",
        "download the wetv",
        "join membership",
        "facebook group",
        "watch more episodes",
        "all dramas are officially",
        "郑重声明",
        "鄭重聲明",
        "这里是专属",
        "這裡是專屬",
    )
    if any(marker in result.lower() for marker in boilerplate_markers):
        result = clean_text(title, 340)
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
            r"\b(auction|sold)\b|拍卖|拍賣|绑架|綁架",
            "主角被绑架后送上地下拍卖台，买下她的人却和她以为已经死去的爱人有关。她必须先逃出去，再查清对方的真实身份。",
            "画面：主角戴着锁链被推上拍卖台，神秘买家高价拍下她。｜吸睛点：人身危机和身份悬念同时出现。",
        ),
        (
            r"\b(cheat(?:ed|ing)?|betray(?:ed|al)?|mistress|divorce|revenge|reborn|regret|ex)\b|出轨|出軌|背叛|复仇|復仇|报仇|報仇|重生|逆天改命|陷害|算计|算計|捉奸|捉姦|欺凌",
            "主角发现最亲近的人背叛了自己。她不再继续忍耐，而是离开旧关系并准备让背叛者付出代价。",
            "画面：主角当场撞破背叛，对方还以为她会继续忍。｜吸睛点：先把委屈压到最低，观众会马上等她反击。",
        ),
        (
            r"\b(wedding|bride|fianc(?:e|é)e?|altar|marriage|contract wife|fake wife)\b|婚礼|婚禮|婚约|婚約|替嫁|新婚|先婚后爱|先婚後愛|婚姻",
            "一纸婚约把原本没有感情的两个人绑在一起。两人从互相提防到逐渐动心，还要处理婚约背后的利益和秘密。",
            "画面：双方当场签下婚约或被迫成为夫妻，彼此都强调这只是一场交易。｜吸睛点：先定规则再埋下动心反转，关系张力马上成立。",
        ),
        (
            r"\b(time travel|another world|system|read mind|mind-reading|apocalypse|zombie)\b|穿越|系统|系統|外挂|外掛|异世界|異世界|读心|讀心|末日|造景箱|物资|物資",
            "主角意外获得穿越、系统或读心等特殊能力，并被卷入一个陌生局面。主角必须先摸清能力规则，再用信息差改变自己的处境。",
            "画面：主角刚确认自己身处异常世界，特殊能力或任务提示立刻出现。｜吸睛点：十秒内同时交代新规则和第一道生存难题。",
        ),
        (
            r"\b(baby|daughter|son|mom|mommy|mother|pregnant|child)\b|萌宝|萌寶|孩子|女儿|女兒|母亲|母親|妈咪|媽咪|怀孕|懷孕",
            "孩子或母亲的身份被人隐瞒，主角因此失去了最重要的家人。她开始追查真相，也逼迫伤害家人的人面对后果。",
            "画面：一个胎记、孕检结果或孩子的称呼突然暴露关系。｜吸睛点：认亲信息一出现，心疼和悬念会同时拉满。",
        ),
        (
            r"\b(bodyguard|guard|protect(?:or|ion)?|rescue)\b|保镖|保鏢|贴身守护|貼身守護|相救|护妻|護妻",
            "主角因身体或身份陷入危险，被安排与贴身保护者朝夕相处。两人一边躲避外部威胁，一边逐渐越过雇佣关系的界线。",
            "画面：危险突然逼近，保护者把主角拉到身后并正面挡下攻击。｜吸睛点：救命动作直接建立关系，也留下保护者身份悬念。",
        ),
        (
            r"\b(billionaire|mafia|king|queen|boss|heiress|heir|ceo|empress)\b|豪门|豪門|黑帮|黑幫|千金|总裁|總裁|霸总|霸總|大佬|女帝|皇后|王妃",
            "主角原本被当成普通人或牺牲品，随后却被真正有权势的人选中。隐藏身份曝光后，原先欺负她的人开始后悔。",
            "画面：主角刚被看不起，真正掌权的人就走到她身边并公开护住她。｜吸睛点：地位在十秒内翻转，爽点非常直接。",
        ),
        (
            r"\b(professor|school|college|student|class)\b|校园|校園|学校|學校|同学|同學",
            "主角在校园里遇到一段不能公开的关系。两人越想装作陌生，过去的秘密越容易被其他人发现。",
            "画面：两人在课堂重新见面并立刻认出对方，却必须假装从未认识。｜吸睛点：观众先知道秘密，会一直等它被戳破。",
        ),
        (
            r"\b(love(?:s|d)?|couple|romance|relationship|chase (?:his|her) wife|game between)\b|爱情|愛情|恋爱|戀愛|追妻|感情|夫妻|情侣|情侶|告白",
            "两个人因为约定或试探被迫靠近，表面上都不肯先认真。随着相处升级，关系中的秘密和真实感情开始暴露。",
            "画面：两人先定下不能动心的规则，下一秒却发生越界接触。｜吸睛点：嘴硬和行动形成反差，观众会等谁先失守。",
        ),
        (
            r"\b(secret|hidden|identity|dragon rider|superhero|genie|magic)\b|隐藏身份|隱藏身份|龙骑|龍騎|神权|神權|封神|仙宗|武功|天尊",
            "所有人都看错了主角的身份。危机出现后，主角露出真正能力，并开始清算曾经羞辱自己的人。",
            "画面：主角先被当成弱者，下一秒直接亮出隐藏能力。｜吸睛点：外表和实力反差越大，打脸越快。",
        ),
        (
            r"军户|軍戶|王朝|皇权|皇權|乱世|亂世|上阵|上陣|家国|家國|江山",
            "主角被卷入乱世或权力争夺，只能靠自己的本事保住身边的人。随着实力提升，主角也开始改变原本注定的结局。",
            "画面：主角刚落入绝境，马上用一项压箱底的本事扭转局面。｜吸睛点：生存目标和升级路线同时出现。",
        ),
    ]
    for pattern, story, first_ten in rules:
        if re.search(pattern, text, re.I):
            return {"story": story, "ten": first_ten}
    return {
        "story": "主角一开场就被卷入一场突发冲突。为了摆脱眼前困境，主角必须马上做出选择，并找出幕后真正的操控者。",
        "ten": "画面：主角的目标刚出现，眼前的阻碍就立刻打断计划。｜吸睛点：人物要什么、谁在阻止，十秒内交代清楚。",
    }


def normalized_copy(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").lower())


def same_card_copy(left: str, right: str) -> bool:
    left_key, right_key = normalized_copy(left), normalized_copy(right)
    return bool(left_key and right_key and left_key == right_key)


def repair_card_copy(item: dict) -> None:
    """Replace title-derived placeholders without overwriting reviewed copy."""
    fallback = direct_card_copy(str(item.get("t", "")), str(item.get("h", "")))
    hook = str(item.get("h", ""))
    story = str(item.get("story", ""))
    ten = str(item.get("ten", ""))
    generic_story = "主角一开场就被卷入一场突发冲突。为了摆脱眼前困境，主角必须马上做出选择，并找出幕后真正的操控者。"
    generic_ten = "画面：主角的目标刚出现，眼前的阻碍就立刻打断计划。｜吸睛点：人物要什么、谁在阻止，十秒内交代清楚。"
    story_needs_repair = (
        not story
        or story == generic_story
        or same_card_copy(story, hook)
        or same_card_copy(story, str(item.get("t", "")))
    )
    if story_needs_repair:
        item["story"] = fallback["story"]
    if (
        story_needs_repair
        or same_card_copy(str(item.get("story", "")), fallback["story"])
        or not ten
        or ten == generic_ten
        or same_card_copy(ten, hook)
        or same_card_copy(ten, story)
    ):
        item["ten"] = fallback["ten"]
    if isinstance(item.get("m"), list) and item["m"] and isinstance(item["m"][0], list):
        item["m"][0][1] = item["ten"]


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


def parse_watch_metadata_html(page: str) -> tuple[int, dict | None]:
    """Read exact duration and the highest-resolution YouTube storyboard level."""
    duration_match = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', page or "")
    duration = int(duration_match.group(1)) if duration_match else 0
    spec_match = re.search(
        r'"storyboards"\s*:\s*\{\s*"playerStoryboardSpecRenderer"\s*:\s*\{\s*"spec"\s*:\s*"((?:\\.|[^"\\])*)"',
        page or "",
    )
    if not spec_match:
        return duration, None
    try:
        spec = json.loads(f'"{spec_match.group(1)}"')
    except json.JSONDecodeError:
        return duration, None

    parts = spec.split("|")
    if len(parts) < 2:
        return duration, None
    base_url = parts[0]
    levels: list[dict] = []
    for level_index, payload in enumerate(parts[1:]):
        fields = payload.split("#")
        if len(fields) < 8:
            continue
        try:
            width, height, count, cols, rows, interval_ms = map(int, fields[:6])
        except ValueError:
            continue
        if min(width, height, count, cols, rows, interval_ms) <= 0:
            continue
        template = base_url.replace("$L", str(level_index)).replace("$N", fields[6])
        template += ("&" if "?" in template else "?") + f"sigh={fields[7]}"
        levels.append(
            {
                "url": template,
                "w": width,
                "h": height,
                "count": count,
                "cols": cols,
                "rows": rows,
                "ms": interval_ms,
            }
        )
    if not levels:
        return duration, None
    return duration, max(levels, key=lambda level: level["w"] * level["h"])


def parse_storyboard_spec(spec: str) -> dict | None:
    """Convert YouTube's pipe-delimited storyboard spec into a crop recipe."""
    parts = str(spec or "").split("|")
    if len(parts) < 2:
        return None
    base_url = parts[0]
    levels: list[dict] = []
    for level_index, payload in enumerate(parts[1:]):
        fields = payload.split("#")
        if len(fields) < 8:
            continue
        try:
            width, height, count, cols, rows, interval_ms = map(int, fields[:6])
        except ValueError:
            continue
        if min(width, height, count, cols, rows, interval_ms) <= 0:
            continue
        template = base_url.replace("$L", str(level_index)).replace("$N", fields[6])
        template += ("&" if "?" in template else "?") + f"sigh={fields[7]}"
        levels.append(
            {
                "url": template,
                "w": width,
                "h": height,
                "count": count,
                "cols": cols,
                "rows": rows,
                "ms": interval_ms,
            }
        )
    return max(levels, key=lambda level: level["w"] * level["h"]) if levels else None


def parse_player_response(payload: dict) -> tuple[int, dict | None]:
    """Read duration and storyboard metadata from an InnerTube player response."""
    details = payload.get("videoDetails") or {}
    try:
        duration = int(details.get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        duration = 0
    renderer = (payload.get("storyboards") or {}).get("playerStoryboardSpecRenderer") or {}
    return duration, parse_storyboard_spec(renderer.get("spec", ""))


def fetch_innertube_metadata(video_id: str) -> tuple[int, dict | None]:
    """Use YouTube's documented-in-practice player endpoint as a metadata fallback.

    The public watch page is increasingly bot-protected on hosted CI runners.  The
    embed page still exposes the current public API key and client version, so we
    reuse those values instead of hard-coding a key that may expire.  This endpoint
    is metadata-only here; no stream URL or account credential is requested.
    """
    embed_url = f"https://www.youtube.com/embed/{video_id}?hl=en"
    embed_page = fetch_browser_bytes(embed_url).decode("utf-8", "replace")
    key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', embed_page)
    version_match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', embed_page)
    visitor_match = re.search(r'"visitorData"\s*:\s*"([^"]+)"', embed_page)
    if not key_match or not version_match:
        return 0, None
    api_key = key_match.group(1)
    client_version = version_match.group(1)
    visitor_data = visitor_match.group(1) if visitor_match else ""

    clients = (
        ("WEB_EMBEDDED_PLAYER", "56", True),
        # TVHTML5_SIMPLY often retains duration when WEB is rate-limited.  It does
        # not normally return storyboards, but is useful as a duration fallback.
        ("TVHTML5_SIMPLY", "75", False),
    )
    duration = 0
    storyboard = None
    for client_name, client_id, embedded in clients:
        client = {
            "clientName": client_name,
            "clientVersion": client_version if embedded else "1.0",
            "hl": "en",
            "gl": "US",
        }
        if visitor_data:
            client["visitorData"] = visitor_data
        context = {"client": client}
        if embedded:
            context["thirdParty"] = {"embedUrl": "https://ghang4587-hub.github.io/hanbox/"}
        body = {
            "videoId": video_id,
            "context": context,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }
        request = urllib.request.Request(
            f"{INNERTUBE_PLAYER_URL}&key={urllib.parse.quote(api_key)}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": BROWSER_USER_AGENT,
                "Origin": "https://www.youtube.com",
                "Referer": embed_url,
                "X-YouTube-Client-Name": client_id,
                "X-YouTube-Client-Version": client["clientVersion"],
            },
        )
        try:
            payload = json.loads(urllib.request.urlopen(request, timeout=35).read())
        except Exception:
            continue
        found_duration, found_storyboard = parse_player_response(payload)
        duration = duration or found_duration
        storyboard = storyboard or found_storyboard
        if duration and storyboard:
            break
    return duration, storyboard


def fetch_watch_metadata(video_id: str) -> tuple[int, dict | None]:
    query = urllib.parse.urlencode({"v": video_id, "hl": "en", "bpctr": "9999999999"})
    duration = 0
    storyboard = None
    try:
        page = fetch_browser_bytes(f"https://www.youtube.com/watch?{query}").decode("utf-8", "replace")
        duration, storyboard = parse_watch_metadata_html(page)
    except Exception:
        pass
    if not duration or not storyboard:
        fallback_duration, fallback_storyboard = fetch_innertube_metadata(video_id)
        duration = duration or fallback_duration
        storyboard = storyboard or fallback_storyboard
    return duration, storyboard


def frame_path(video_id: str, seconds: int) -> Path:
    return FRAMES_ROOT / video_id / f"{seconds:03d}.jpg"


def existing_frames(video_id: str) -> dict[str, str]:
    return {
        str(seconds): frame_path(video_id, seconds).relative_to(ROOT).as_posix()
        for seconds in FRAME_SECONDS
        if frame_path(video_id, seconds).is_file()
    }


def capture_storyboard_frames(video_id: str, spec: dict) -> dict[str, str]:
    """Crop ten real frames from YouTube's official storyboard sheets."""
    from PIL import Image, ImageFilter, ImageOps

    target_dir = FRAMES_ROOT / video_id
    target_dir.mkdir(parents=True, exist_ok=True)
    sheet_cache: dict[int, Image.Image] = {}
    captured: dict[str, str] = {}
    cells_per_sheet = spec["cols"] * spec["rows"]
    for seconds in FRAME_SECONDS:
        frame_index = min(spec["count"] - 1, max(0, seconds * 1000 // spec["ms"]))
        sheet_index, cell_index = divmod(frame_index, cells_per_sheet)
        if sheet_index not in sheet_cache:
            sheet_url = spec["url"].replace("$M", str(sheet_index))
            sheet_cache[sheet_index] = Image.open(io.BytesIO(fetch_browser_bytes(sheet_url))).convert("RGB")
        sheet = sheet_cache[sheet_index]
        left = (cell_index % spec["cols"]) * spec["w"]
        top = (cell_index // spec["cols"]) * spec["h"]
        frame = sheet.crop((left, top, left + spec["w"], top + spec["h"]))

        background = ImageOps.fit(frame, (640, 360), method=Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(18)).point(lambda value: int(value * 0.62))
        foreground = ImageOps.contain(frame, (640, 360), method=Image.Resampling.LANCZOS)
        background.paste(foreground, ((640 - foreground.width) // 2, (360 - foreground.height) // 2))
        output = frame_path(video_id, seconds)
        background.save(output, "JPEG", quality=80, optimize=True, progressive=True)
        captured[str(seconds)] = output.relative_to(ROOT).as_posix()
    return captured


def enrich_weekly_media(videos: list[dict]) -> None:
    """Fill duration and frame assets for the current week's retained videos."""
    FRAMES_ROOT.mkdir(parents=True, exist_ok=True)
    weekly = sorted(
        (item for item in videos if is_current_week(item)),
        key=popularity_key,
        reverse=True,
    )[:MEDIA_ENRICH_LIMIT]
    for item in weekly:
        video_id = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
            continue
        frames = existing_frames(video_id)
        if len(frames) == len(FRAME_SECONDS) and int(item.get("d") or 0) > 0:
            item["frames"] = frames
            continue
        try:
            duration, storyboard = fetch_watch_metadata(video_id)
            if duration:
                item["d"] = duration
            if storyboard and len(frames) < len(FRAME_SECONDS):
                frames = capture_storyboard_frames(video_id, storyboard)
                item["framesCapturedAt"] = datetime.now(timezone.utc).isoformat()
            if storyboard:
                # Keep the signed storyboard recipe in the feed as a fallback for
                # browsers that can render the sheets but cannot receive binary
                # assets from the runner.  The URL is restricted to i.ytimg.com
                # when the static page normalizes it.
                item["storyboard"] = storyboard
            if frames:
                item["frames"] = frames
            print(f"media {video_id}: duration={item.get('d', 0)} frames={len(frames)}")
        except Exception as exc:
            print(f"warning: media capture failed for {video_id}: {exc}", file=sys.stderr)


def prune_frame_assets(videos: list[dict]) -> None:
    if not FRAMES_ROOT.exists():
        return
    keep_ids = {str(item.get("id")) for item in videos if item.get("id")}
    for path in FRAMES_ROOT.iterdir():
        if path.is_dir() and path.name not in keep_ids:
            shutil.rmtree(path)


def generic_moments(hook: str) -> list[list[str]]:
    return [
        ["00:00", hook],
        ["00:58", "冲突升级，隐藏关系或真实目的被揭开。"],
        ["01:46", "对立双方被迫正面交锋，情绪开始翻面。"],
        ["02:43", "新线索出现，留下下一集悬念。"],
    ]


COMPLETE_TITLE_MARKERS = (
    "合集",
    "全集",
    "完整版",
    "完整劇",
    "完整剧",
    "full version",
    "full movie",
    "full drama",
    "【full】",
    "[full]",
    "complete",
    "all episodes",
)


def is_episode_range_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    return bool(
        re.search(r"\bep(?:isode)?\s*0*\d+\s*(?:-|–|—|~|至|到)\s*(?:ep(?:isode)?\s*)?0*\d+\b", normalized)
        or re.search(r"第\s*\d+\s*(?:-|–|—|~|至|到)\s*\d+\s*集", normalized)
    )


def is_complete_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    return any(marker in normalized for marker in COMPLETE_TITLE_MARKERS) or is_episode_range_title(title)


def is_single_episode_title(title: str) -> bool:
    """Reject standalone episode uploads while retaining episode ranges/compilations."""
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    if not normalized or is_complete_title(title):
        return False

    return bool(
        re.search(r"\bep(?:isode)?\s*0*\d+\b", normalized)
        or re.search(r"第\s*\d+\s*集", normalized)
    )


def is_eligible_title(title: str, excluded_keywords: list[str]) -> bool:
    normalized = (title or "").lower()
    if any(keyword in normalized for keyword in excluded_keywords):
        return False
    return not is_single_episode_title(title)


def is_eligible_source_title(channel: dict, title: str, excluded_keywords: list[str]) -> bool:
    return is_eligible_title(title, excluded_keywords) and (
        not channel.get("completeOnly") or is_complete_title(title)
    )


def build_entry(
    channel: dict,
    video_id: str,
    title: str,
    description: str,
    published: str,
    views: int = 0,
    duration: int = 0,
    source: str = "youtube-atom",
) -> dict:
    hook = synopsis_from_description(description, title)
    story_source = synopsis_from_description(description, title, limit=260)
    card_copy = direct_card_copy(title, hook)
    translated_hook = translate_to_zh(hook)
    display_hook = translated_hook or hook
    translated_story = translate_to_zh(story_source)
    entry = {
        "id": video_id,
        "t": title,
        "c": channel["label"],
        "market": channel.get("market", "overseas"),
        "platform": channel.get("platform", channel["label"]),
        "d": duration,
        "s": hook_score(title, hook),
        "g": tags_for(title, description),
        "h": display_hook,
        "hEn": hook,
        "ten": card_copy["ten"],
        "story": translated_story or card_copy["story"],
        "u": "先验证前 180 秒的身份、羞辱或反击节点，再决定是否拆成买量素材。",
        "m": generic_moments(card_copy["ten"]),
        "v": views,
        "p": published,
        "source": source,
        "sourceUrl": f"https://www.youtube.com/watch?v={video_id}",
    }
    repair_card_copy(entry)
    return entry


def feed_entries(channel: dict, excluded_keywords: list[str] | None = None) -> list[dict]:
    source_id = str(channel.get("playlistId") or channel.get("id") or "").strip()
    if not source_id:
        return []
    feed_key = "playlist_id" if channel.get("playlistId") else "channel_id"
    url = f"https://www.youtube.com/feeds/videos.xml?{feed_key}={urllib.parse.quote(source_id)}"
    root = ET.fromstring(fetch_bytes(url))
    result: list[dict] = []
    excluded = [str(keyword).lower() for keyword in (excluded_keywords or []) if keyword]
    for entry in root.findall("atom:entry", ATOM_NS):
        video_id = text_at(entry, "yt:videoId")
        title = clean_text(text_at(entry, "atom:title"), 180)
        if not is_eligible_source_title(channel, title, excluded):
            continue
        published = text_at(entry, "atom:published")
        description = ""
        for node in entry.iter("{%s}description" % ATOM_NS["media"]):
            description = node.text or ""
            break
        if not video_id or not title:
            continue
        result.append(build_entry(channel, video_id, title, description, published, parse_views(entry)))
    return result


def api_source_entries(channel: dict, api_key: str, excluded_keywords: list[str]) -> list[dict]:
    """List source videos through the low-cost uploads/playlist Data API endpoints."""
    playlist_id = str(channel.get("playlistId") or "").strip()
    if not playlist_id:
        query = urllib.parse.urlencode(
            {"part": "contentDetails", "id": channel["id"], "key": api_key}
        )
        payload = json.loads(fetch_bytes(f"https://www.googleapis.com/youtube/v3/channels?{query}"))
        records = payload.get("items") or []
        if not records:
            return []
        playlist_id = (
            records[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        )
    if not playlist_id:
        return []

    query = urllib.parse.urlencode(
        {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key,
        }
    )
    payload = json.loads(fetch_bytes(f"https://www.googleapis.com/youtube/v3/playlistItems?{query}"))
    result = []
    for record in payload.get("items") or []:
        snippet = record.get("snippet") or {}
        video_id = str((record.get("contentDetails") or {}).get("videoId") or "")
        title = clean_text(str(snippet.get("title") or ""), 180)
        if not video_id or not title or not is_eligible_source_title(channel, title, excluded_keywords):
            continue
        result.append(
            build_entry(
                channel,
                video_id,
                title,
                str(snippet.get("description") or ""),
                str((record.get("contentDetails") or {}).get("videoPublishedAt") or snippet.get("publishedAt") or ""),
                source="youtube-data-api",
            )
        )
    return result


def walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def text_contents(value: object):
    for node in walk_dicts(value):
        for key in ("content", "simpleText", "text"):
            text = node.get(key)
            if isinstance(text, str) and text:
                yield text


def parse_compact_views(values: list[str]) -> int:
    for value in values:
        match = re.search(r"([\d,.]+)\s*([kmb]?)\s+views?\b", value.lower())
        if not match:
            continue
        number = float(match.group(1).replace(",", ""))
        multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[match.group(2)]
        return int(number * multiplier)
    return 0


def parse_relative_published(values: list[str]) -> str:
    for value in values:
        match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", value.lower())
        if not match:
            continue
        amount, unit = int(match.group(1)), match.group(2)
        days = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}[unit]
        published = datetime.now(timezone.utc) - (
            timedelta(minutes=amount) if unit == "minute" else
            timedelta(hours=amount) if unit == "hour" else
            timedelta(days=amount * days)
        )
        return published.isoformat()
    return ""


def parse_badge_duration(value: object) -> int:
    for text in text_contents(value):
        if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
            continue
        parts = [int(part) for part in text.split(":")]
        return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def web_source_entries(channel: dict, excluded_keywords: list[str]) -> list[dict]:
    """Fallback for sources whose legacy Atom feed is unavailable."""
    if channel.get("playlistId"):
        url = f"https://www.youtube.com/playlist?list={urllib.parse.quote(channel['playlistId'])}"
    else:
        url = f"https://www.youtube.com/channel/{urllib.parse.quote(channel['id'])}/videos"
    page = fetch_browser_bytes(url).decode("utf-8", "replace")
    match = re.search(r"var ytInitialData = (\{.+?\});</script>", page)
    if not match:
        raise ValueError("YouTube page did not expose initial video data")
    initial_data = json.loads(match.group(1))
    result = []
    seen = set()
    for node in walk_dicts(initial_data):
        lockup = node.get("lockupViewModel")
        if not isinstance(lockup, dict) or lockup.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            continue
        video_id = str(lockup.get("contentId") or "")
        metadata = (lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {}
        title = clean_text(str((metadata.get("title") or {}).get("content") or ""), 180)
        if video_id in seen or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        seen.add(video_id)
        if not title or not is_eligible_source_title(channel, title, excluded_keywords):
            continue
        metadata_values = list(text_contents(metadata.get("metadata") or {}))
        result.append(
            build_entry(
                channel,
                video_id,
                title,
                title,
                parse_relative_published(metadata_values),
                parse_compact_views(metadata_values),
                parse_badge_duration(lockup.get("contentImage") or {}),
                "youtube-web-fallback",
            )
        )
        if len(result) >= 40:
            break
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


def parse_published(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_current_week(item: dict, now: datetime | None = None) -> bool:
    published = parse_published(item.get("p"))
    if published is None:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=current.weekday())
    return start <= published < start + timedelta(days=7)


def popularity_key(item: dict) -> tuple[int, float]:
    try:
        views = int(item.get("v") or 0)
    except (TypeError, ValueError):
        views = 0
    published = parse_published(item.get("p"))
    return views, published.timestamp() if published else 0.0


def normalized_market(item: dict) -> str:
    market = str(item.get("market") or "").lower()
    if market in {"domestic", "overseas"}:
        return market
    channel = str(item.get("c") or "")
    return "domestic" if re.search(r"优酷|阅文|腾讯|红果|河马|七猫|星芽|快手|抖音", channel) else "overseas"


def select_market_sample(videos: list[dict], market: str) -> list[dict]:
    pool = [item for item in videos if normalized_market(item) == market]
    weekly = sorted(
        (item for item in pool if is_current_week(item)),
        key=popularity_key,
        reverse=True,
    )[:WEEKLY_NEW_LIMIT_PER_MARKET]
    selected_ids = {item.get("id") for item in weekly}
    selected = list(weekly)

    # Reserve useful representation for each head platform before filling by
    # overall popularity.  Weekly releases already count toward the quota.
    for platform, quota in PLATFORM_SAMPLE_QUOTAS.get(market, {}).items():
        platform_count = sum(item.get("platform") == platform for item in selected)
        candidates = sorted(
            (
                item
                for item in pool
                if item.get("platform") == platform and item.get("id") not in selected_ids
            ),
            key=popularity_key,
            reverse=True,
        )
        for item in candidates[: max(0, quota - platform_count)]:
            selected.append(item)
            selected_ids.add(item.get("id"))

    remaining = sorted(
        (item for item in pool if item.get("id") not in selected_ids),
        key=popularity_key,
        reverse=True,
    )
    selected.extend(remaining[: max(0, MARKET_SAMPLE_LIMIT - len(selected))])
    return selected[:MARKET_SAMPLE_LIMIT]


def mark_weekly_focus(videos: list[dict]) -> None:
    """Mark 8 actionable references per market, preferring this week's releases."""
    for item in videos:
        item["focus"] = False
    for market in ("overseas", "domestic"):
        market_items = [item for item in videos if normalized_market(item) == market]
        ranked = sorted(
            market_items,
            key=lambda item: (is_current_week(item), int(item.get("s") or 0), *popularity_key(item)),
            reverse=True,
        )[:WEEKLY_FOCUS_LIMIT_PER_MARKET]
        for item in ranked:
            item["focus"] = True


def select_sample(videos: list[dict]) -> list[dict]:
    """Keep independent 24-item domestic and overseas rolling samples."""
    selected = select_market_sample(videos, "overseas") + select_market_sample(videos, "domestic")
    selected_ids = {item.get("id") for item in selected}
    if len(selected) < SAMPLE_LIMIT:
        remainder = sorted(
            (item for item in videos if item.get("id") not in selected_ids),
            key=popularity_key,
            reverse=True,
        )
        selected.extend(remainder[: SAMPLE_LIMIT - len(selected)])
    selected = selected[:SAMPLE_LIMIT]
    for item in selected:
        item["market"] = normalized_market(item)
    mark_weekly_focus(selected)
    return selected


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    excluded_keywords = [
        str(keyword).lower()
        for keyword in config.get("excludeTitleKeywords", [])
        if keyword
    ]
    channels = [
        channel
        for channel in config.get("channels", [])
        if channel.get("id") or channel.get("playlistId")
    ]
    if not channels:
        print("No YouTube channels configured", file=sys.stderr)
        return 2

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    fetched: list[dict] = []
    for channel in channels:
        entries: list[dict] = []
        api_error = None
        feed_error = None
        if api_key:
            try:
                entries = api_source_entries(channel, api_key, excluded_keywords)
            except Exception as exc:
                api_error = exc
        if not entries:
            try:
                entries = feed_entries(channel, excluded_keywords)
            except Exception as exc:
                feed_error = exc
        if not entries:
            try:
                entries = web_source_entries(channel, excluded_keywords)
            except Exception as web_error:
                details = f"API={api_error}; " if api_error else ""
                atom_details = f"Atom={feed_error}; " if feed_error else "Atom returned no eligible entries; "
                print(
                    f"warning: failed to fetch {channel['label']}: "
                    f"{details}{atom_details}web={web_error}",
                    file=sys.stderr,
                )
                continue
            if entries:
                atom_reason = str(feed_error) if feed_error else "no eligible entries"
                print(
                    f"{channel['label']}: Atom unavailable ({atom_reason}); "
                    f"using {len(entries)} public-page entries"
                )
        if entries:
            print(f"{channel['label']}: {len(entries)} eligible entries")
            fetched.extend(entries)

    if api_key:
        try:
            api_enrich(fetched, api_key)
            print("enriched durations and view counts with YouTube Data API")
        except Exception as exc:
            print(f"warning: API enrichment failed; keeping RSS values: {exc}", file=sys.stderr)

    existing = load_existing()
    channels_by_label = {str(channel.get("label") or ""): channel for channel in channels}
    retained_previous = []
    for previous in existing.get("videos", []):
        source_channel = channels_by_label.get(str(previous.get("c") or ""), {})
        if not is_eligible_source_title(
            source_channel, str(previous.get("t") or ""), excluded_keywords
        ):
            continue
        if source_channel:
            previous["market"] = source_channel.get("market", normalized_market(previous))
            previous["platform"] = source_channel.get("platform", previous.get("c", ""))
        retained_previous.append(previous)
    removed_count = len(existing.get("videos", [])) - len(retained_previous)
    if removed_count:
        print(f"removed {removed_count} ineligible cached records")
    existing["videos"] = retained_previous
    for previous in retained_previous:
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
        repair_card_copy(previous)
    by_id = {item.get("id"): item for item in existing.get("videos", []) if item.get("id")}
    for item in fetched:
        previous = by_id.get(item["id"])
        if previous:
            # Keep any manually edited analysis while refreshing factual fields.
            if previous.get("source") in {
                "youtube-atom",
                "youtube-data-api",
                "youtube-web-fallback",
            }:
                for key in ("s", "g", "h", "hEn", "ten", "story", "u", "m"):
                    previous[key] = item[key]
            elif item.get("story"):
                previous["story"] = item["story"]
            for key in ("t", "c", "market", "platform", "d", "v", "p", "source", "sourceUrl", "storyboard", "frames", "framesCapturedAt"):
                if item.get(key) not in (None, "", 0) or key in ("t", "c", "market", "platform", "p", "source", "sourceUrl"):
                    previous[key] = item.get(key)
        else:
            by_id[item["id"]] = item

    videos = select_sample(list(by_id.values()))
    enrich_weekly_media(videos)
    prune_frame_assets(videos)
    existing.update(
        {
            "version": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": "YouTube public channel feeds" + (" + Data API" if api_key else ""),
            "videos": videos,
        }
    )
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(existing['videos'])} weekly records to {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

