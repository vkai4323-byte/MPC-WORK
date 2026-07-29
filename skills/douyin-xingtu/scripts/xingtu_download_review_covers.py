from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import urllib.request


def download_one(task: tuple[str, Path]) -> bool:
    url, target = task
    if target.exists():
        return True
    if not url:
        return False
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            target.write_bytes(response.read())
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--creators", type=int, default=100)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, Path]] = []
    records = data.get("records") or []
    for index, record in enumerate(records[: args.creators], start=1):
        for item_index, item in enumerate(record.get("recent_items") or [], start=1):
            tasks.append(
                (
                    str(item.get("cover_url") or ""),
                    output_dir / f"{index:03d}_{item_index}.jpg",
                )
            )
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(download_one, tasks))
    print(
        json.dumps(
            {
                "creators": min(args.creators, len(records)),
                "requested": len(tasks),
                "downloaded": sum(results),
                "output_dir": str(output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
