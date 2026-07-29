#!/usr/bin/env python3
"""Build a name-matched TSV paste block for POPO clipboard fallback.

This helper does not read the full workbook, write ShareDB state, or prove full-sheet coverage.
Callers must establish total/scanned row equality and the complete eligible row/key set separately.

Inputs:
- source: Markdown table, TSV, or CSV containing a talent/name column and target columns.
- popo: TSV copied from POPO containing one header row, the sheet name column, and target columns.

Outputs:
- paste TSV with only target columns, one row per POPO data row.
- match report TSV describing match status for each POPO row plus source-only rows.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


NAME_CANDIDATES = ("达人", "达人名称", "博主", "姓名", "昵称", "账号", "达人名", "name", "talent")

PRESET_TARGETS = {
    "talent-basic": [
        ("fans", ("粉丝量(w)", "粉丝量", "粉丝", "达人粉丝", "粉丝数", "fans")),
        ("homepage", ("主页链接", "达人主页", "首页链接", "主页", "链接", "homepage", "url")),
        ("rate", ("刊例价", "报价", "平台报价", "视频报价", "21-60s报价", "21-60s视频报价", "rate", "price")),
        ("rate_screenshot", ("平台价截图", "报价截图", "刊例截图", "星图报价截图", "截图", "screenshot")),
    ],
}


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).casefold()


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def parse_markdown_table(text: str) -> list[dict[str, str]]:
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows[1:]]


def parse_delimited(text: str, delimiter: str) -> list[dict[str, str]]:
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [{key or "": value or "" for key, value in row.items()} for row in reader]


def parse_table(path: str) -> list[dict[str, str]]:
    text = read_text(path)
    md_rows = parse_markdown_table(text)
    if md_rows:
        return md_rows
    if "\t" in text.splitlines()[0]:
        return parse_delimited(text, "\t")
    return parse_delimited(text, ",")


def find_col(rows: list[dict[str, str]], requested: str | None, candidates: tuple[str, ...] = NAME_CANDIDATES) -> str:
    if not rows:
        raise SystemExit("Input table has no rows")
    columns = list(rows[0].keys())
    if requested:
        if requested in columns:
            return requested
        raise SystemExit(f"Column not found: {requested}. Available: {', '.join(columns)}")
    normalized = {normalize_name(col): col for col in columns}
    for candidate in candidates:
        key = normalize_name(candidate)
        if key in normalized:
            return normalized[key]
    raise SystemExit(f"Name column not found. Available: {', '.join(columns)}")


def find_optional_col(rows: list[dict[str, str]], candidates: tuple[str, ...]) -> str | None:
    if not rows:
        return None
    normalized = {normalize_name(col): col for col in rows[0].keys()}
    for candidate in candidates:
        key = normalize_name(candidate)
        if key in normalized:
            return normalized[key]
    return None


def split_targets(value: str | None) -> list[str]:
    if value is None:
        return []
    targets = [part.strip() for part in value.split(",") if part.strip()]
    return targets


def resolve_targets(popo_rows: list[dict[str, str]], explicit_targets: str | None, preset: str | None) -> list[str]:
    targets = split_targets(explicit_targets)
    if targets:
        return targets
    if not preset:
        raise SystemExit("--target-cols is required unless --preset is supplied")
    if preset not in PRESET_TARGETS:
        raise SystemExit(f"Unknown preset: {preset}. Available: {', '.join(PRESET_TARGETS)}")
    resolved: list[str] = []
    for _, candidates in PRESET_TARGETS[preset]:
        col = find_optional_col(popo_rows, candidates)
        if col:
            resolved.append(col)
    if not resolved:
        available = ", ".join(popo_rows[0].keys()) if popo_rows else ""
        raise SystemExit(f"Preset {preset} found no target columns. Available: {available}")
    return resolved


def source_col_for_target(source_row: dict[str, str], target: str, source_map: dict[str, str], preset: str | None) -> str | None:
    requested = source_map.get(target, target)
    if requested in source_row:
        return requested
    if preset and preset in PRESET_TARGETS:
        target_key = normalize_name(target)
        for _, candidates in PRESET_TARGETS[preset]:
            candidate_keys = {normalize_name(candidate) for candidate in candidates}
            if target_key in candidate_keys:
                found = find_optional_col([source_row], candidates)
                if found:
                    return found
    return None


def parse_source_map_arg(value: str | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not value:
        return mapping
    for part in value.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise SystemExit("--source-map entries must look like POPO_COL=SOURCE_COL")
        left, right = part.split("=", 1)
        mapping[left.strip()] = right.strip()
    return mapping


def build_index(rows: list[dict[str, str]], name_col: str) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    index: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = normalize_name(row.get(name_col, ""))
        if key:
            index.setdefault(key, []).append(row)
    duplicates = sorted(key for key, values in index.items() if len(values) > 1)
    return index, duplicates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Source Markdown/TSV/CSV file")
    parser.add_argument("--popo", required=True, help="TSV copied from POPO, with header row")
    parser.add_argument("--name-col", help="Name column header used in both files when shared")
    parser.add_argument("--source-name-col", help="Name column header in source file")
    parser.add_argument("--popo-name-col", help="Name column header in copied POPO TSV")
    parser.add_argument("--target-cols", help="Comma-separated POPO target columns")
    parser.add_argument("--preset", choices=sorted(PRESET_TARGETS), help="Auto-detect common target columns")
    parser.add_argument("--source-map", help="Comma-separated POPO_COL=SOURCE_COL overrides")
    parser.add_argument("--out-paste", required=True)
    parser.add_argument("--out-report", required=True)
    args = parser.parse_args()

    source_rows = parse_table(args.source)
    popo_rows = parse_table(args.popo)
    target_cols = resolve_targets(popo_rows, args.target_cols, args.preset)
    source_map = parse_source_map_arg(args.source_map)

    source_name_col = find_col(source_rows, args.source_name_col or args.name_col)
    popo_name_col = find_col(popo_rows, args.popo_name_col or args.name_col)

    source_index, duplicate_source = build_index(source_rows, source_name_col)
    popo_index, duplicate_popo = build_index(popo_rows, popo_name_col)

    paste_rows: list[list[str]] = []
    report_rows: list[dict[str, str]] = []
    used_source_keys: set[str] = set()

    for pos, popo_row in enumerate(popo_rows, start=1):
        raw_name = popo_row.get(popo_name_col, "")
        key = normalize_name(raw_name)
        status = "missing_source"
        matched = None
        if key in duplicate_popo:
            status = "duplicate_popo"
        elif key in duplicate_source:
            status = "duplicate_source"
        elif key in source_index:
            matched = source_index[key][0]
            used_source_keys.add(key)
            status = "matched"

        out_row: list[str] = []
        for target in target_cols:
            if target not in popo_row:
                raise SystemExit(f"POPO target column not found: {target}")
            source_col = source_col_for_target(matched, target, source_map, args.preset) if matched else None
            if matched is not None and source_col:
                out_row.append(matched.get(source_col, ""))
            else:
                out_row.append(popo_row.get(target, ""))
        paste_rows.append(out_row)
        report_rows.append({
            "kind": "popo_row",
            "row": str(pos),
            "name": raw_name,
            "status": status,
            "target_values": " | ".join(out_row),
        })

    for key, rows in sorted(source_index.items()):
        if key not in used_source_keys and key not in popo_index:
            report_rows.append({
                "kind": "source_only",
                "row": "",
                "name": rows[0].get(source_name_col, ""),
                "status": "source_only",
                "target_values": "",
            })

    with Path(args.out_paste).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerows(paste_rows)

    with Path(args.out_report).open("w", encoding="utf-8", newline="") as fh:
        fieldnames = ["kind", "row", "name", "status", "target_values"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(report_rows)

    matched_count = sum(1 for row in report_rows if row["status"] == "matched")
    conflict_count = sum(1 for row in report_rows if row["status"].startswith("duplicate"))
    missing_count = sum(1 for row in report_rows if row["status"] == "missing_source")
    source_only_count = sum(1 for row in report_rows if row["status"] == "source_only")
    print(
        f"matched={matched_count} conflicts={conflict_count} "
        f"missing_source={missing_count} source_only={source_only_count} "
        f"target_cols={','.join(target_cols)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
