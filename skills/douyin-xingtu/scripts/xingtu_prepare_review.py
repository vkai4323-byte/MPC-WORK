from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any
import urllib.parse

import xingtu_direct as xd
import xingtu_similarity as xs

NON_CREATOR_NAME_TERMS = [
    "动漫展", "漫展", "收藏夹", "摄影", "工作室", "解说", "资讯", "攻略",
    "官方", "助眠", "妆教", "娱乐", "女团", "保安", "大爷", "tv",
]


def embedded(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def get_items(session: dict[str, Any], item_ids: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    ids = list(dict.fromkeys(item_ids))
    for offset in range(0, len(ids), 20):
        group = ids[offset : offset + 20]
        query = urllib.parse.urlencode(
            {
                "platform_source": 1,
                "item_ids": ",".join(group),
                "use_cache": "false",
                "need_cover_url": "true",
            },
            safe=",",
        )
        payload = xd.request_xingtu(
            session,
            "GET",
            f"{xd.ITEM_ENDPOINT}?{query}",
        )
        for item in payload.get("items") or []:
            result[str(item.get("id") or "")] = item
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--creators", type=int, default=115)
    parser.add_argument("--items-per-creator", type=int, default=2)
    parser.add_argument("--personal-only", action="store_true")
    args = parser.parse_args()

    merged: dict[str, dict[str, Any]] = {}
    for input_path in args.input:
        source = json.loads(Path(input_path).read_text(encoding="utf-8"))
        for row in source["authors"]:
            identity = xs.creator_identity(row)
            if not identity:
                continue
            if identity not in merged:
                merged[identity] = row
                merged[identity]["_query_hits"] = []
            for query in row.get("_query_hits") or []:
                if query not in merged[identity]["_query_hits"]:
                    merged[identity]["_query_hits"].append(query)
    ranked = list(merged.values())
    for row in ranked:
        row["_similarity"] = xs.score_author(row)
    ranked.sort(
        key=lambda row: (
            {"A": 2, "B": 1, "C": 0}[row["_similarity"]["tier"]],
            row["_similarity"]["score"],
            int((row.get("attribute_datas") or {}).get("follower") or 0),
        ),
        reverse=True,
    )
    if args.personal_only:
        ranked = [
            row for row in ranked
            if row["_similarity"]["required"]["female"]
            and row["_similarity"]["required"]["cosplay_evidence"]
            and not any(
                term in str((row.get("attribute_datas") or {}).get("nick_name") or "").lower()
                for term in NON_CREATOR_NAME_TERMS
            )
        ]
    selected = [
        row for row in ranked
        if (row.get("attribute_datas") or {}).get("nick_name") != "虎纹章鱼"
    ][: args.creators]

    requested_ids: list[str] = []
    creator_recent: dict[str, list[dict[str, Any]]] = {}
    for author in selected:
        attrs = author.get("attribute_datas") or {}
        core_id = str(attrs.get("core_user_id") or author.get("star_id") or "")
        recent = embedded(attrs.get("last_10_items"), [])
        creator_recent[core_id] = recent
        requested_ids.extend(
            str(item.get("item_id"))
            for item in recent[: args.items_per_creator]
            if item.get("item_id")
        )

    details = get_items(xd.load_session(), requested_ids)
    records: list[dict[str, Any]] = []
    for author in selected:
        attrs = author.get("attribute_datas") or {}
        core_id = str(attrs.get("core_user_id") or author.get("star_id") or "")
        recent = creator_recent.get(core_id, [])
        review_items = []
        for summary in recent[: args.items_per_creator]:
            item_id = str(summary.get("item_id") or "")
            detail = details.get(item_id) or {}
            review_items.append(
                {
                    "item_id": item_id,
                    "title": detail.get("title") or summary.get("item_title") or "",
                    "cover_url": detail.get("cover_url") or "",
                    "current_plays": (detail.get("stats") or {}).get("watch_cnt"),
                    "cached_plays": summary.get("vv"),
                    "duration": detail.get("duration"),
                    "url": detail.get("url"),
                }
            )
        records.append(
            {
                "core_user_id": core_id,
                "star_id": str(author.get("star_id") or attrs.get("id") or ""),
                "nick_name": attrs.get("nick_name") or "",
                "gender": attrs.get("gender"),
                "follower": attrs.get("follower"),
                "price_1_20": attrs.get("price_1_20"),
                "vv_median_30d": attrs.get("vv_median_30d"),
                "labels": embedded(attrs.get("content_theme_labels_180d"), []),
                "tags_relation": embedded(attrs.get("tags_relation"), {}),
                "similarity": author.get("_similarity") or {},
                "recent_items": review_items,
                "avatar_url": attrs.get("avatar_uri") or "",
            }
        )

    output = {
        "records": records,
        "item_details_requested": len(requested_ids),
        "item_details_received": len(details),
        "observed_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(target.resolve()),
                "records": len(records),
                "item_details_received": len(details),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
