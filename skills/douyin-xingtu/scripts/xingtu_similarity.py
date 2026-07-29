from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Any

import xingtu_direct as xd


DEFAULT_QUERIES = [
    "coser搞笑",
    "cosplay搞笑",
    "二次元整活",
    "抽象cos",
    "反差cos",
    "美女整活",
    "二次元美女",
    "游戏cos",
    "动漫cos",
    "漫展coser",
    "cos变装",
    "原神cos",
    "星穹铁道cos",
    "王者荣耀cos",
]

COS_TERMS = [
    "cos", "coser", "cosplay", "二次元", "角色扮演", "漫展", "宅物手办",
    "原神", "崩坏", "星穹铁道", "王者荣耀", "第五人格", "蛋仔", "动漫",
    "游戏角色", "国乙", "乙游", "洛丽塔", "lo娘",
]
HUMOR_TERMS = [
    "搞笑", "抽象", "整活", "反差", "沙雕", "发疯", "玩梗", "魔性", "离谱",
    "搞怪", "神经", "脑洞", "逗比", "哈基米", "咕咕嘎嘎", "曼波", "变装挑战",
    "模仿", "段子", "社死", "逆天", "显眼包",
]
BEAUTY_TERMS = [
    "美女", "颜值", "甜妹", "御姐", "萌妹", "妆造", "变装", "仿妆", "美少女",
    "人像", "小姐姐", "女神",
]
EXCLUDE_TERMS = [
    "婆媳", "家庭短剧", "乡村", "农村", "亲子", "母婴", "育儿", "影视解说",
    "影视剪辑", "搬运", "剧情盘点", "社会新闻", "萌娃", "广场舞",
]


def parse_embedded(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def creator_identity(author: dict[str, Any]) -> str:
    attrs = author.get("attribute_datas") or {}
    return str(
        attrs.get("core_user_id")
        or author.get("star_id")
        or attrs.get("id")
        or ""
    )


def searchable_text(author: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    attrs = author.get("attribute_datas") or {}
    recent = parse_embedded(attrs.get("last_10_items"), [])
    labels = parse_embedded(attrs.get("content_theme_labels_180d"), [])
    tags = parse_embedded(attrs.get("tags_relation"), {})
    chunks = [
        str(attrs.get("nick_name") or ""),
        " ".join(str(x) for x in labels),
        json.dumps(tags, ensure_ascii=False),
        " ".join(str(x.get("item_title") or "") for x in recent),
    ]
    return " ".join(chunks).lower(), recent


def hits(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term.lower() in text]


def score_author(author: dict[str, Any]) -> dict[str, Any]:
    attrs = author.get("attribute_datas") or {}
    text, recent = searchable_text(author)
    cos_hits = hits(text, COS_TERMS)
    humor_hits = hits(text, HUMOR_TERMS)
    beauty_hits = hits(text, BEAUTY_TERMS)
    exclusion_hits = hits(text, EXCLUDE_TERMS)
    gender = str(attrs.get("gender") or "")
    query_hits = sorted(author.get("_query_hits") or [])

    recent_cos = sum(
        any(term.lower() in str(item.get("item_title") or "").lower() for term in COS_TERMS)
        for item in recent
    )
    recent_humor = sum(
        any(term.lower() in str(item.get("item_title") or "").lower() for term in HUMOR_TERMS)
        for item in recent
    )
    score = 0
    score += 20 if gender == "2" else -22
    score += min(30, len(cos_hits) * 5)
    score += min(25, len(humor_hits) * 5)
    score += min(10, len(beauty_hits) * 3)
    score += min(12, recent_cos * 3)
    score += min(12, recent_humor * 3)
    score += min(8, len(query_hits) * 2)
    score -= 35 * len(exclusion_hits)

    required = {
        "female": gender == "2",
        "cosplay_evidence": bool(cos_hits) and recent_cos > 0,
        "humor_evidence": bool(humor_hits) or any(
            key in query for query in query_hits
            for key in ("搞笑", "抽象", "整活", "反差")
        ),
        "no_conflicting_format": not exclusion_hits,
    }
    if all(required.values()):
        tier = "A"
    elif required["female"] and required["cosplay_evidence"] and required["no_conflicting_format"]:
        tier = "B"
    else:
        tier = "C"

    return {
        "score": score,
        "tier": tier,
        "required": required,
        "cos_hits": cos_hits,
        "humor_hits": humor_hits,
        "beauty_hits": beauty_hits,
        "exclusion_hits": exclusion_hits,
        "recent_cos_count": recent_cos,
        "recent_humor_count": recent_humor,
        "query_hits": query_hits,
    }


def search_one(
    session: dict[str, Any], keyword: str, pages: int, limit: int
) -> tuple[str, list[dict[str, Any]]]:
    authors: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        payload = xd.request_xingtu(
            session,
            "POST",
            xd.SEARCH_ENDPOINT,
            xd.build_search_payload(
                session["search_template"], keyword, "content", page, limit
            ),
        )
        page_authors = payload.get("authors") or []
        authors.extend(page_authors)
        if len(page_authors) < limit:
            break
    return keyword, authors


def command_discover(args: argparse.Namespace) -> None:
    session = xd.load_session()
    queries = args.query or DEFAULT_QUERIES
    merged: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(search_one, session, query, args.pages, args.limit)
            for query in queries
        ]
        for future in concurrent.futures.as_completed(futures):
            query, authors = future.result()
            for author in authors:
                identity = creator_identity(author)
                if not identity:
                    continue
                if identity not in merged:
                    author["_query_hits"] = []
                    merged[identity] = author
                if query not in merged[identity]["_query_hits"]:
                    merged[identity]["_query_hits"].append(query)

    ranked: list[dict[str, Any]] = []
    for author in merged.values():
        author["_similarity"] = score_author(author)
        ranked.append(author)
    ranked.sort(
        key=lambda row: (
            {"A": 2, "B": 1, "C": 0}[row["_similarity"]["tier"]],
            row["_similarity"]["score"],
            int((row.get("attribute_datas") or {}).get("follower") or 0),
        ),
        reverse=True,
    )
    output = {
        "target_archetype": {
            "required": [
                "女性真人出镜",
                "近期作品存在COS/二次元角色呈现",
                "近期内容存在抽象、整活、反差或搞笑表达",
            ],
            "excluded": EXCLUDE_TERMS,
            "note": "标签只用于召回；最终选择必须结合近期标题与封面人工复核。",
        },
        "queries": queries,
        "authors": ranked,
        "counts": {
            "unique": len(ranked),
            "tier_a": sum(x["_similarity"]["tier"] == "A" for x in ranked),
            "tier_b": sum(x["_similarity"]["tier"] == "B" for x in ranked),
        },
        "observed_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"output": str(target.resolve()), **output["counts"]},
            ensure_ascii=False,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and pre-score Xingtu creators against a content archetype"
    )
    parser.add_argument("--query", action="append")
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", required=True)
    parser.set_defaults(func=command_discover)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
