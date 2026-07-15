#!/usr/bin/env python3
"""Batch public Bilibili video, creator, and search data."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine
from urllib.parse import urlparse

VENDORED_DEPS = Path(__file__).resolve().parents[1] / ".deps"
if VENDORED_DEPS.is_dir():
    sys.path.insert(0, str(VENDORED_DEPS))

BVID_RE = re.compile(r"(?i)(BV[0-9A-Za-z]{10})")
SPACE_HOST = "space.bilibili.com"
REQUEST_TIMEOUT_SECONDS = 30.0


def parse_identity(value: str) -> tuple[str | None, int | None]:
    """Parse a video BV/AV identity. Bare digits remain backward-compatible AIDs."""
    text = str(value).strip()
    bvid = BVID_RE.search(text)
    if bvid:
        return bvid.group(1), None
    if re.fullmatch(r"(?i)av\d+", text):
        return None, int(text[2:])
    if re.fullmatch(r"\d{4,}", text):
        return None, int(text)
    aid = re.search(r"(?i)/(?:video/)?av(\d+)", text)
    return (None, int(aid.group(1))) if aid else (None, None)


def parse_creator_mid(value: Any, *, allow_numeric: bool) -> int | None:
    """Parse a creator MID without confusing a video AID with a creator UID."""
    if allow_numeric and isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    text = str(value).strip()
    if allow_numeric and re.fullmatch(r"\d+", text):
        mid = int(text)
        return mid if mid > 0 else None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or (parsed.hostname or "").lower() != SPACE_HOST:
        return None
    first_segment = next((part for part in parsed.path.split("/") if part), "")
    if not re.fullmatch(r"\d+", first_segment):
        return None
    mid = int(first_segment)
    return mid if mid > 0 else None


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split()).casefold()


def load_items(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError("input JSON must be an array")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            obj = {"value": item}
        elif isinstance(item, dict):
            obj = dict(item)
        else:
            raise ValueError(f"video input item {index} must be a string or object")
        source = obj.get("bvid") or obj.get("aid") or obj.get("url") or obj.get("value") or ""
        bvid, aid = parse_identity(str(source))
        items.append({"index": index, "key": obj.get("key", str(source)), "bvid": bvid, "aid": aid})
    return items


def load_creator_items(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError("creator input JSON must be an array")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            source: Any = item
            mid = parse_creator_mid(source, allow_numeric=False)
            key = item
            expected_name = None
        elif isinstance(item, dict):
            obj = dict(item)
            has_mid = obj.get("mid") is not None or obj.get("uid") is not None
            source = obj.get("mid") if obj.get("mid") is not None else obj.get("uid")
            if source is None:
                source = obj.get("space_url") or obj.get("url") or obj.get("value") or ""
            mid = parse_creator_mid(source, allow_numeric=has_mid)
            key = obj.get("key", str(source))
            expected_name = obj.get("expected_name") or obj.get("name")
        else:
            raise ValueError(f"creator input item {index} must be a space URL string or object")
        items.append({
            "index": index,
            "key": key,
            "mid": mid,
            "source": str(source),
            "expected_name": expected_name,
        })
    return items


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_rate_limited(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    return code == -412 or code == 412 or "412" in str(exc)


def describe_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


async def bounded_call(
    factory: Callable[[], Coroutine[Any, Any, Any]],
    semaphore: asyncio.Semaphore,
    serial_lock: asyncio.Lock,
    retries: int,
    backoff_base: float = 1.5,
) -> tuple[Any | None, BaseException | None]:
    """Retry normal errors finitely; a 412 gets exactly one serial retry."""
    normal_failures = 0
    serial_retry = False
    while True:
        try:
            if serial_retry:
                async with serial_lock:
                    async with semaphore:
                        return await asyncio.wait_for(factory(), timeout=REQUEST_TIMEOUT_SECONDS), None
            async with semaphore:
                return await asyncio.wait_for(factory(), timeout=REQUEST_TIMEOUT_SECONDS), None
        except Exception as exc:  # The library exposes several transport exception types.
            if is_rate_limited(exc):
                if serial_retry:
                    return None, exc
                serial_retry = True
                await asyncio.sleep(backoff_base)
                continue
            if normal_failures >= retries:
                return None, exc
            normal_failures += 1
            await asyncio.sleep(backoff_base * (2 ** (normal_failures - 1)))


def normalize_video(raw: dict[str, Any], source: str) -> dict[str, Any]:
    bvid = raw.get("bvid")
    aid = raw.get("aid")
    return {
        "bvid": bvid,
        "aid": aid,
        "title": raw.get("title"),
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
        "pubdate": raw.get("created") if raw.get("created") is not None else raw.get("pubdate"),
        "duration": raw.get("length") if raw.get("length") is not None else raw.get("duration"),
        "play": raw.get("play") if raw.get("play") is not None else raw.get("view"),
        "description": raw.get("description") if raw.get("description") is not None else raw.get("desc"),
        "source": source,
    }


def extract_creator_videos(data: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    listing = data.get("list")
    if isinstance(listing, dict):
        raw_videos = listing.get("vlist") or []
    else:
        raw_videos = data.get("res") or data.get("result") or []
    if not isinstance(raw_videos, list):
        return []
    return [normalize_video(item, source) for item in raw_videos if isinstance(item, dict)]


def normalize_user_candidate(raw: dict[str, Any], keyword: str) -> dict[str, Any]:
    mid = raw.get("mid")
    try:
        mid = int(mid)
    except (TypeError, ValueError):
        mid = None
    name = raw.get("uname") or raw.get("name")
    return {
        "mid": mid,
        "name": name,
        "normalized_name": normalize_name(name),
        "exact_name": bool(name) and normalize_name(name) == normalize_name(keyword),
        "sign": raw.get("usign") or raw.get("sign"),
        "followers": raw.get("fans") if raw.get("fans") is not None else raw.get("follower"),
        "video_count": raw.get("videos"),
        "face": raw.get("upic") or raw.get("face"),
        "verified": raw.get("verify_info") or raw.get("official"),
        "level": raw.get("level"),
        "is_upuser": raw.get("is_upuser"),
        "recent_videos": extract_creator_videos(raw, "user-search"),
    }


async def fetch_video(
    item: dict[str, Any],
    video_mod: Any,
    semaphore: asyncio.Semaphore,
    serial_lock: asyncio.Lock,
    retries: int,
) -> dict[str, Any]:
    base = {"key": item["key"], "bvid": item["bvid"], "aid": item["aid"]}
    if not item["bvid"] and not item["aid"]:
        return {**base, "status": "invalid", "error": "No BV/AV id found", "observed_at": observed_at()}
    obj = video_mod.Video(bvid=item["bvid"]) if item["bvid"] else video_mod.Video(aid=item["aid"])
    info, error = await bounded_call(obj.get_info, semaphore, serial_lock, retries)
    if error or not isinstance(info, dict):
        return {**base, "status": "error", "error": describe_error(error), "observed_at": observed_at()}
    stat, owner = info.get("stat") or {}, info.get("owner") or {}
    bvid = info.get("bvid") or item["bvid"]
    return {
        **base,
        "bvid": bvid,
        "aid": info.get("aid") or item["aid"],
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
        "title": info.get("title"),
        "owner": owner.get("name"),
        "owner_mid": owner.get("mid"),
        "view": stat.get("view"),
        "danmaku": stat.get("danmaku"),
        "pubdate": info.get("pubdate"),
        "observed_at": observed_at(),
        "status": "ready",
        "error": None,
    }


async def run_video_batch(items: list[dict[str, Any]], concurrency: int, retries: int) -> list[dict[str, Any]]:
    try:
        from bilibili_api import video
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    semaphore = asyncio.Semaphore(concurrency)
    serial_lock = asyncio.Lock()
    identities: dict[tuple[str | None, int | None], dict[str, Any]] = {}
    for item in items:
        identities.setdefault((item["bvid"], item["aid"]), item)
    identity_keys = list(identities)
    fetched = await asyncio.gather(*(
        fetch_video(identities[key], video, semaphore, serial_lock, retries) for key in identity_keys
    ))
    by_identity = dict(zip(identity_keys, fetched))
    return [{**by_identity[(item["bvid"], item["aid"])], "key": item["key"]} for item in items]


async def search_user_candidates(
    keyword: str,
    page: int,
    page_size: int,
    search_mod: Any,
    semaphore: asyncio.Semaphore,
    serial_lock: asyncio.Lock,
    retries: int,
) -> tuple[list[dict[str, Any]], BaseException | None]:
    factory = lambda: search_mod.search_by_type(
        keyword=keyword,
        search_type=search_mod.SearchObjectType.USER,
        page=page,
        page_size=page_size,
    )
    data, error = await bounded_call(factory, semaphore, serial_lock, retries)
    if error or not isinstance(data, dict):
        return [], error
    raw_candidates = data.get("result") or []
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    return [normalize_user_candidate(item, keyword) for item in raw_candidates if isinstance(item, dict)], None


async def fetch_creator(
    item: dict[str, Any],
    recent: int,
    user_mod: Any,
    search_mod: Any,
    semaphore: asyncio.Semaphore,
    serial_lock: asyncio.Lock,
    retries: int,
) -> dict[str, Any]:
    mid = item.get("mid")
    base = {
        "key": item["key"],
        "mid": mid,
        "canonical_url": f"https://space.bilibili.com/{mid}" if mid else None,
        "recent_requested": recent,
    }
    if not mid:
        return {
            **base,
            "status": "invalid",
            "recent_returned": 0,
            "recent_videos": [],
            "evidence": {"source_id": None, "url": None, "observed_at": observed_at()},
            "errors": ["No valid creator MID/space URL found"],
        }

    obj = user_mod.User(mid)
    errors: list[str] = []
    info, info_error = await bounded_call(obj.get_user_info, semaphore, serial_lock, retries)
    if info_error:
        errors.append(f"user_info: {describe_error(info_error)}")
        info = {}
    relation, relation_error = await bounded_call(obj.get_relation_info, semaphore, serial_lock, retries)
    if relation_error:
        errors.append(f"relation_info: {describe_error(relation_error)}")
        relation = {}
    videos_data, videos_error = await bounded_call(
        lambda: obj.get_videos(pn=1, ps=recent, order=user_mod.VideoOrder.PUBDATE),
        semaphore,
        serial_lock,
        retries,
    )
    recent_videos = extract_creator_videos(videos_data, "creator-videos")
    fallback_candidate: dict[str, Any] | None = None

    name = (info or {}).get("name") or item.get("expected_name")
    if videos_error:
        errors.append(f"creator_videos: {describe_error(videos_error)}")
    if (videos_error or not info) and name:
        candidates, search_error = await search_user_candidates(
            name, 1, max(20, recent), search_mod, semaphore, serial_lock, 1
        )
        if search_error:
            errors.append(f"user_search_fallback: {describe_error(search_error)}")
        else:
            fallback_candidate = next((candidate for candidate in candidates if candidate.get("mid") == mid), None)
            if fallback_candidate and not recent_videos:
                recent_videos = fallback_candidate.get("recent_videos") or []

    info = info if isinstance(info, dict) else {}
    relation = relation if isinstance(relation, dict) else {}
    page = videos_data.get("page") if isinstance(videos_data, dict) else {}
    page = page if isinstance(page, dict) else {}
    fallback_candidate = fallback_candidate or {}
    name = info.get("name") or fallback_candidate.get("name") or item.get("expected_name")
    recent_videos = recent_videos[:recent]

    result = {
        **base,
        "name": name,
        "sign": info.get("sign") or fallback_candidate.get("sign"),
        "face": info.get("face") or fallback_candidate.get("face"),
        "official": info.get("official") or fallback_candidate.get("verified"),
        "level": info.get("level") or fallback_candidate.get("level"),
        "followers": relation.get("follower") if relation.get("follower") is not None else fallback_candidate.get("followers"),
        "following": relation.get("following"),
        "video_total": page.get("count") if page.get("count") is not None else fallback_candidate.get("video_count"),
        "recent_returned": len(recent_videos),
        "recent_complete": len(recent_videos) >= recent,
        "recent_fetch_status": "error" if videos_error else "short" if len(recent_videos) < recent else "complete",
        "recent_videos": recent_videos,
        "evidence": {
            "source_id": str(mid),
            "url": f"https://space.bilibili.com/{mid}",
            "observed_at": observed_at(),
        },
        "errors": errors,
    }
    has_trusted_public_data = bool(recent_videos) or relation.get("follower") is not None or page.get("count") is not None
    if not name and not has_trusted_public_data:
        result["status"] = "error"
    elif not name or len(recent_videos) < recent or errors:
        result["status"] = "partial"
    else:
        result["status"] = "ready"
    result["incomplete"] = result["status"] in {"partial", "error"}
    return result


async def run_creator_batch(
    items: list[dict[str, Any]],
    recent: int,
    concurrency: int,
    retries: int,
) -> list[dict[str, Any]]:
    try:
        from bilibili_api import search, user
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    semaphore = asyncio.Semaphore(concurrency)
    serial_lock = asyncio.Lock()
    identities: dict[int | None, dict[str, Any]] = {}
    for item in items:
        identities.setdefault(item["mid"], item)
    identity_keys = list(identities)
    fetched = await asyncio.gather(*(
        fetch_creator(identities[mid], recent, user, search, semaphore, serial_lock, retries)
        for mid in identity_keys
    ))
    by_mid = dict(zip(identity_keys, fetched))
    return [{**by_mid[item["mid"]], "key": item["key"]} for item in items]


async def run_search(keyword: str, page: int, retries: int) -> dict[str, Any]:
    try:
        from bilibili_api import search
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    data, error = await bounded_call(
        lambda: search.search(keyword=keyword, page=page),
        asyncio.Semaphore(1),
        asyncio.Lock(),
        retries,
    )
    if error:
        raise RuntimeError(describe_error(error))
    return {"keyword": keyword, "page": page, "observed_at": observed_at(), "status": "ready", "data": data}


async def run_user_search(keyword: str, page: int, page_size: int, retries: int) -> dict[str, Any]:
    try:
        from bilibili_api import search
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    candidates, error = await search_user_candidates(
        keyword,
        page,
        page_size,
        search,
        asyncio.Semaphore(1),
        asyncio.Lock(),
        retries,
    )
    if error:
        return {
            "keyword": keyword,
            "page": page,
            "page_size": page_size,
            "observed_at": observed_at(),
            "status": "error",
            "candidates": [],
            "error": describe_error(error),
        }
    exact_count = sum(1 for item in candidates if item["exact_name"])
    status = "empty" if not candidates else "ambiguous" if exact_count > 1 else "ready" if exact_count == 1 else "candidates"
    return {
        "keyword": keyword,
        "page": page,
        "page_size": page_size,
        "observed_at": observed_at(),
        "status": status,
        "candidates": candidates,
        "error": None,
    }


async def self_test_bounded_call() -> int:
    class RateLimited(Exception):
        code = 412

    attempts = 0

    async def succeeds_after_rate_limit() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimited("412")
        return "ok"

    result, error = await bounded_call(
        succeeds_after_rate_limit, asyncio.Semaphore(2), asyncio.Lock(), retries=5, backoff_base=0
    )
    assert result == "ok" and error is None and attempts == 2

    attempts = 0

    async def always_rate_limited() -> None:
        nonlocal attempts
        attempts += 1
        raise RateLimited("412")

    result, error = await bounded_call(
        always_rate_limited, asyncio.Semaphore(2), asyncio.Lock(), retries=5, backoff_base=0
    )
    assert result is None and is_rate_limited(error) and attempts == 2
    return 2


def self_test() -> dict[str, Any]:
    tests = 0
    try:
        import bilibili_api
        from bilibili_api import get_registered_clients, search, user, video
    except ImportError as exc:
        raise AssertionError(
            "Missing dependency: install requirements.txt or provide the ignored .deps directory"
        ) from exc

    module_path = Path(bilibili_api.__file__).resolve()
    dependency_source = (
        "vendored" if VENDORED_DEPS.is_dir() and VENDORED_DEPS.resolve() in module_path.parents else "environment"
    )
    clients = sorted(get_registered_clients())
    if not any("httpx" in name.lower() for name in clients):
        raise AssertionError(f"httpx client is not registered: {clients}")
    tests += 2

    cases = {
        "BV1uv411q7Mv": ("BV1uv411q7Mv", None),
        "https://www.bilibili.com/video/BV1uv411q7Mv?p=1": ("BV1uv411q7Mv", None),
        "av243922477": (None, 243922477),
        "https://www.bilibili.com/video/av243922477": (None, 243922477),
        "243922477": (None, 243922477),
    }
    for value, expected in cases.items():
        assert parse_identity(value) == expected, (value, parse_identity(value), expected)
        tests += 1

    creator_cases = {
        "https://space.bilibili.com/20841379": 20841379,
        "https://space.bilibili.com/20841379/": 20841379,
        "https://space.bilibili.com/20841379/upload/video?x=1": 20841379,
        "https://evil.com/space.bilibili.com/20841379": None,
        "https://space.bilibili.com/abc": None,
        "20841379": None,
    }
    for value, expected in creator_cases.items():
        assert parse_creator_mid(value, allow_numeric=False) == expected
        tests += 1
    assert parse_creator_mid(20841379, allow_numeric=True) == 20841379
    assert parse_creator_mid("20841379", allow_numeric=True) == 20841379
    assert parse_creator_mid(0, allow_numeric=True) is None
    tests += 3

    fixture = normalize_video(
        {"bvid": "BV1uv411q7Mv", "title": "t", "created": 1, "length": "1:00", "play": 2, "description": "d"},
        "fixture",
    )
    assert fixture["pubdate"] == 1 and fixture["duration"] == "1:00" and fixture["play"] == 2
    assert callable(user.User.get_user_info) and callable(user.User.get_videos)
    assert user.User(2).get_uid() == 2
    assert search.SearchObjectType.USER.value == "bili_user"
    assert video.Video is not None
    tests += 5
    tests += asyncio.run(self_test_bounded_call())

    return {
        "status": "ok",
        "dependency_source": dependency_source,
        "version": getattr(bilibili_api, "BILIBILI_API_VERSION", "unknown"),
        "clients": clients,
        "tests": tests,
    }


def bounded_int(minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch public Bilibili video, creator, and search data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--input", type=Path, help="JSON array of video URLs/IDs or keyed objects")
    mode.add_argument("--search", help="Bilibili keyword search")
    mode.add_argument("--creator-input", type=Path, help="JSON array of keyed creator MID/space URL objects")
    mode.add_argument("--user-search", help="Bilibili creator-name search; returns candidates without auto-selecting")
    parser.add_argument("--page", type=bounded_int(1, 1000), default=1)
    parser.add_argument("--page-size", type=bounded_int(1, 50), default=20)
    parser.add_argument("--recent", type=bounded_int(1, 50), default=10)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=bounded_int(1, 8), default=2)
    parser.add_argument("--retries", type=bounded_int(0, 5), default=2)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        try:
            result = self_test()
        except Exception as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not any((args.input, args.search, args.creator_input, args.user_search)):
        parser.error("one of --input, --search, --creator-input, --user-search, or --self-test is required")

    try:
        if args.search:
            result = asyncio.run(run_search(args.search, args.page, args.retries))
        elif args.user_search:
            result = asyncio.run(run_user_search(args.user_search, args.page, args.page_size, args.retries))
        elif args.creator_input:
            result = asyncio.run(run_creator_batch(
                load_creator_items(args.creator_input), args.recent, args.concurrency, args.retries
            ))
        else:
            result = asyncio.run(run_video_batch(load_items(args.input), args.concurrency, args.retries))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
