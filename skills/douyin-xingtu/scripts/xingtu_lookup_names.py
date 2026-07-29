from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any

import xingtu_direct as xd


def normalize(value: str) -> str:
    return re.sub(r"[\s·•._\-—（）()【】\[\]🍬🎀]+", "", value).lower()


def lookup(
    session: dict[str, Any], name: str, limit: int
) -> tuple[str, list[dict[str, Any]]]:
    payload = xd.request_xingtu(
        session,
        "POST",
        xd.SEARCH_ENDPOINT,
        xd.build_search_payload(
            session["search_template"], name, "nickname", 1, limit
        ),
    )
    return name, payload.get("authors") or []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    session = xd.load_session()
    matched: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(lookup, session, name, args.limit)
            for name in args.name
        ]
        for future in concurrent.futures.as_completed(futures):
            name, authors = future.result()
            exact = []
            target = normalize(name)
            for author in authors:
                attrs = author.get("attribute_datas") or {}
                nickname = str(attrs.get("nick_name") or "")
                if normalize(nickname) == target:
                    exact.append(author)
            if len(exact) == 1:
                exact[0]["_query_hits"] = [f"外部内容发现:{name}"]
                matched.append(exact[0])
            else:
                unresolved.append(
                    {
                        "name": name,
                        "exact_count": len(exact),
                        "returned_names": [
                            (row.get("attribute_datas") or {}).get("nick_name")
                            for row in authors[:5]
                        ],
                    }
                )
    output = {
        "authors": matched,
        "unresolved": unresolved,
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
                "matched": len(matched),
                "unresolved": len(unresolved),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
