#!/usr/bin/env python3
"""Batch Bilibili public search/video metadata through bilibili-api-python."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VENDORED_DEPS = Path(__file__).resolve().parents[1] / ".deps"
if VENDORED_DEPS.is_dir():
    sys.path.insert(0, str(VENDORED_DEPS))

BVID_RE = re.compile(r"(?i)(BV[0-9A-Za-z]{10})")


def parse_identity(value: str) -> tuple[str | None, int | None]:
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


def load_items(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError("input JSON must be an array")
    items = []
    for index, item in enumerate(raw):
        obj = {"value": item} if isinstance(item, str) else dict(item)
        source = obj.get("bvid") or obj.get("aid") or obj.get("url") or obj.get("value") or ""
        bvid, aid = parse_identity(str(source))
        items.append({"index": index, "key": obj.get("key", str(source)), "bvid": bvid, "aid": aid})
    return items


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def fetch_video(item: dict[str, Any], video_mod: Any, semaphore: asyncio.Semaphore, retries: int) -> dict[str, Any]:
    base = {"key": item["key"], "bvid": item["bvid"], "aid": item["aid"]}
    if not item["bvid"] and not item["aid"]:
        return {**base, "status": "invalid", "error": "No BV/AV id found", "observed_at": observed_at()}
    for attempt in range(retries + 1):
        try:
            async with semaphore:
                obj = video_mod.Video(bvid=item["bvid"]) if item["bvid"] else video_mod.Video(aid=item["aid"])
                info = await obj.get_info()
            stat, owner = info.get("stat") or {}, info.get("owner") or {}
            bvid = info.get("bvid") or item["bvid"]
            return {**base, "bvid": bvid, "aid": info.get("aid") or item["aid"],
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
                    "title": info.get("title"), "owner": owner.get("name"), "owner_mid": owner.get("mid"),
                    "view": stat.get("view"), "danmaku": stat.get("danmaku"), "pubdate": info.get("pubdate"),
                    "observed_at": observed_at(), "status": "ready", "error": None}
        except Exception as exc:
            if attempt >= retries:
                return {**base, "status": "error", "error": str(exc), "observed_at": observed_at()}
            await asyncio.sleep(1.5 * (2**attempt))
    raise AssertionError("unreachable")


async def run_video_batch(items: list[dict[str, Any]], concurrency: int, retries: int) -> list[dict[str, Any]]:
    try:
        from bilibili_api import video
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    semaphore = asyncio.Semaphore(max(1, concurrency))
    identities: dict[tuple[str | None, int | None], dict[str, Any]] = {}
    for item in items:
        identities.setdefault((item["bvid"], item["aid"]), item)
    identity_keys = list(identities)
    fetched = await asyncio.gather(*(fetch_video(identities[key], video, semaphore, retries) for key in identity_keys))
    by_identity = dict(zip(identity_keys, fetched))
    results = []
    for item in items:
        row = by_identity.get((item["bvid"], item["aid"]))
        if row is None:
            row = await fetch_video(item, video, semaphore, 0)
        results.append({**row, "key": item["key"]})
    return results


async def run_search(keyword: str, page: int) -> dict[str, Any]:
    try:
        from bilibili_api import search
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install bilibili-api-python and httpx") from exc
    data = await search.search(keyword=keyword, page=page)
    return {"keyword": keyword, "page": page, "observed_at": observed_at(), "status": "ready", "data": data}


def self_test() -> None:
    cases = {"BV1uv411q7Mv": ("BV1uv411q7Mv", None),
             "https://www.bilibili.com/video/BV1uv411q7Mv?p=1": ("BV1uv411q7Mv", None),
             "av243922477": (None, 243922477),
             "https://www.bilibili.com/video/av243922477": (None, 243922477)}
    for value, expected in cases.items():
        assert parse_identity(value) == expected, (value, parse_identity(value), expected)
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--input", type=Path, help="JSON array of URLs/IDs or keyed objects")
    mode.add_argument("--search", help="Bilibili keyword search")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.input and not args.search:
        parser.error("one of --input, --search, or --self-test is required")
    try:
        result = asyncio.run(run_search(args.search, args.page)) if args.search else asyncio.run(
            run_video_batch(load_items(args.input), args.concurrency, args.retries))
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
