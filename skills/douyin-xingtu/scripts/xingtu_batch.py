#!/usr/bin/env python3
"""Read-only 巨量星图 client through the local Kimi WebBridge daemon."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DAEMON_URL = "http://127.0.0.1:10086/command"
MARKET_URL = "https://www.xingtu.cn/ad/creator/market"
ITEM_ENDPOINT = "/gw/api/data_sp/external_multi_get_item"
RANKING_ENDPOINT = "/gw/api/gsearch/get_ranking_list_data"
TASK_REPORT_ENDPOINT = "/gw/api/data_sp/project_task_report_info"
ITEM_ID_RE = re.compile(r"(?<!\d)(\d{16,22})(?!\d)")
DOUYIN_VIDEO_RE = re.compile(r"(?:video|modal_id)[=/](\d{16,22})")
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))


class XingtuError(RuntimeError):
    pass


class WebBridge:
    def __init__(self, session: str, timeout: int = 30) -> None:
        self.session = session
        self.timeout = timeout

    def _request(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(
            {"action": action, "args": args or {}, "session": self.session},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            DAEMON_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise XingtuError(f"WebBridge unavailable: {exc}") from exc
        if not result.get("ok"):
            raise XingtuError(result.get("error") or f"WebBridge action failed: {action}")
        return result.get("data") or {}

    def call(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(action, args)

    def ensure_market(self) -> dict[str, Any]:
        tabs = self.call("list_tabs").get("tabs") or []
        xingtu_tabs = [tab for tab in tabs if "xingtu.cn" in str(tab.get("url", ""))]
        if xingtu_tabs:
            selected = xingtu_tabs[-1]
            self.call("find_tab", {"url": selected["url"]})
            if "/ad/creator/" not in str(selected.get("url", "")):
                self.call("navigate", {"url": MARKET_URL, "newTab": False})
        else:
            self.call(
                "navigate",
                {"url": MARKET_URL, "newTab": True, "group_title": "抖音星图数据"},
            )
        state = self.evaluate_json(
            """(()=>JSON.stringify({
                href:location.href,
                title:document.title,
                authenticated:/\\/ad\\/creator\\//.test(location.pathname)
            }))()"""
        )
        if not state.get("authenticated"):
            raise XingtuError(
                "auth_required: authenticated Xingtu advertiser page is unavailable"
            )
        return state

    def evaluate_json(self, code: str) -> Any:
        data = self.call("evaluate", {"code": code})
        value = data.get("value")
        if data.get("type") == "string" and isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def fetch_json(self, path: str) -> dict[str, Any]:
        js_path = json.dumps(path, ensure_ascii=False)
        code = (
            "(async()=>{"
            f"const r=await fetch({js_path},{{credentials:'include'}});"
            "const t=await r.text();"
            "return JSON.stringify({ok:r.ok,status:r.status,body_text:t});"
            "})()"
        )
        result = self.evaluate_json(code)
        if not isinstance(result, dict) or not result.get("ok"):
            raise XingtuError(f"Xingtu read failed for {path}: {result}")
        body_text = result.get("body_text")
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else None
        except json.JSONDecodeError as exc:
            raise XingtuError(f"Non-JSON Xingtu response for {path}") from exc
        if not isinstance(body, dict):
            raise XingtuError(f"Unexpected Xingtu response for {path}")
        base = body.get("base_resp")
        if isinstance(base, dict) and base.get("status_code") not in (None, 0):
            raise XingtuError(
                f"Xingtu API error {base.get('status_code')}: "
                f"{base.get('status_message', '')}"
            )
        return body


def observed_at() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def timestamp_to_china(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return dt.datetime.fromtimestamp(int(value), CHINA_TZ).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def normalize_exact(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | None, payload: Any) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"output": str(target.resolve()), "records": _record_count(payload)},
                ensure_ascii=False,
            )
        )
    else:
        print(rendered)


def _record_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("records", "authors", "items", "data"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return 1


def extract_item_id(value: str) -> str | None:
    matched = DOUYIN_VIDEO_RE.search(value)
    if matched:
        return matched.group(1)
    if value.isdigit() and 16 <= len(value) <= 22:
        return value
    matched = ITEM_ID_RE.search(value)
    return matched.group(1) if matched else None


def resolve_short_url(url: str, timeout: int = 15) -> tuple[str, str | None]:
    item_id = extract_item_id(url)
    if item_id:
        return url, item_id
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/136 Safari/537.36"
            )
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            resolved = response.geturl()
            return resolved, extract_item_id(resolved)
    except (urllib.error.URLError, TimeoutError):
        return url, None


def item_inputs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise XingtuError("items input must be a JSON array")
    records: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if isinstance(value, str):
            record = {"source_key": value, "item_url": value}
        elif isinstance(value, dict):
            record = dict(value)
        else:
            raise XingtuError(f"unsupported item input at index {index}")
        record.setdefault(
            "source_key",
            record.get("display_name")
            or record.get("key")
            or record.get("item_id")
            or record.get("item_url")
            or f"item-{index + 1}",
        )
        supplied = str(record.get("item_id") or record.get("item_url") or "")
        item_id = extract_item_id(supplied)
        if not item_id and record.get("item_url"):
            resolved, item_id = resolve_short_url(str(record["item_url"]))
            record["resolved_url"] = resolved
        record["item_id"] = item_id or ""
        records.append(record)
    return records


def resolve_item_urls_with_browser(
    client: WebBridge, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve only URLs that the bounded HTTP redirect path could not resolve."""
    for record in records:
        if record.get("item_id") or not record.get("item_url"):
            continue
        original_url = str(record["item_url"])
        resolved_url = original_url
        opened_tab = False
        try:
            client.call(
                "navigate",
                {"url": original_url, "newTab": True, "group_title": "抖音链接解析"},
            )
            opened_tab = True
            for attempt in range(5):
                state = client.evaluate_json(
                    "(()=>JSON.stringify({href:location.href}))()"
                )
                if isinstance(state, dict):
                    resolved_url = str(state.get("href") or resolved_url)
                    item_id = extract_item_id(resolved_url)
                    if item_id:
                        record["resolved_url"] = resolved_url
                        record["item_id"] = item_id
                        record["url_resolution"] = "browser"
                        break
                if attempt < 4:
                    time.sleep(0.75)
        except XingtuError:
            record.setdefault("resolution_warnings", []).append(
                "browser_short_url_resolution_failed"
            )
        finally:
            if opened_tab:
                try:
                    client.call("close_tab")
                except XingtuError:
                    pass
            client.ensure_market()
    return records


def canonical_item(source: dict[str, Any], item: dict[str, Any], at: str) -> dict[str, Any]:
    stats = item.get("stats") or {}
    item_id = str(item.get("id") or source.get("item_id") or "")
    publish_time = timestamp_to_china(item.get("create_time"))
    missing = [
        name
        for name, value in (
            ("publish_time", publish_time),
            ("play_count", stats.get("watch_cnt")),
        )
        if value in (None, "")
    ]
    return {
        "source_key": source["source_key"],
        "query": {
            "display_name": source.get("display_name", ""),
            "item_url": source.get("item_url", ""),
            "item_id": item_id,
        },
        "identity": {
            "author_id": str(item.get("author_id") or ""),
            "core_user_id": str(item.get("author_id") or ""),
            "star_id": "",
            "match_method": "item_id",
            "confidence": "exact",
        },
        "metrics": {
            "publish_time": publish_time,
            "play_count": _integer(stats.get("watch_cnt")),
            "like_count": _integer(stats.get("like_cnt")),
            "comment_count": _integer(stats.get("comment_cnt")),
            "share_count": _integer(stats.get("share_cnt")),
            "favorite_count": _integer(stats.get("favorite_cnt")),
            "interaction_rate": _number(stats.get("interact_rate")),
            "duration_seconds": _number(item.get("duration")),
        },
        "content": {
            "title": item.get("title", ""),
            "topics": item.get("topic_ids") or [],
            "canonical_url": item.get("url", ""),
        },
        "evidence": [
            {
                "endpoint": ITEM_ENDPOINT,
                "source_field": "stats.watch_cnt",
                "publish_time_source": "create_time",
                "observed_at": at,
            }
        ],
        "observations": [],
        "status": "partial" if missing else "ready",
        "errors": [f"missing:{name}" for name in missing],
    }


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_items(client: WebBridge, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unresolved = [record for record in records if not record.get("item_id")]
    ids = list(dict.fromkeys(str(record["item_id"]) for record in records if record.get("item_id")))
    found: dict[str, dict[str, Any]] = {}
    for group in chunks(ids, 20):
        query = urllib.parse.urlencode(
            {
                "platform_source": 1,
                "item_ids": ",".join(group),
                "use_cache": "false",
                "need_cover_url": "true",
            },
            safe=",",
        )
        body = client.fetch_json(f"{ITEM_ENDPOINT}?{query}")
        for item in body.get("items") or []:
            found[str(item.get("id"))] = item
        missing_group = [item_id for item_id in group if item_id not in found]
        if missing_group:
            fallback_query = urllib.parse.urlencode(
                {
                    "platform_source": 1,
                    "item_ids": ",".join(missing_group),
                    "use_cache": "true",
                    "need_cover_url": "true",
                    "tpl": "tplv-9hvokabxw2-thumbnail",
                    "format": "webp",
                },
                safe=",",
            )
            fallback_body = client.fetch_json(f"{ITEM_ENDPOINT}?{fallback_query}")
            for item in fallback_body.get("items") or []:
                found[str(item.get("id"))] = item
    at = observed_at()
    output: list[dict[str, Any]] = []
    for source in records:
        item_id = str(source.get("item_id") or "")
        if not item_id:
            output.append(
                {
                    "source_key": source["source_key"],
                    "query": source,
                    "status": "not_found",
                    "errors": ["unresolved_item_id"],
                    "evidence": [],
                }
            )
        elif item_id not in found:
            output.append(
                {
                    "source_key": source["source_key"],
                    "query": source,
                    "status": "not_found",
                    "errors": ["item_not_returned"],
                    "evidence": [
                        {
                            "endpoint": ITEM_ENDPOINT,
                            "source_field": "items[].id",
                            "observed_at": at,
                        }
                    ],
                }
            )
        else:
            output.append(canonical_item(source, found[item_id], at))
    assert len(output) == len(records)
    assert len(unresolved) == sum(
        1 for record in output if "unresolved_item_id" in record.get("errors", [])
    )
    return output


def author_inputs(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise XingtuError("authors input must be a JSON array")
    records: list[dict[str, str]] = []
    for index, value in enumerate(raw):
        if isinstance(value, str):
            display_name = value
            source_key = value
        elif isinstance(value, dict):
            display_name = str(
                value.get("display_name")
                or value.get("name")
                or value.get("douyin_id")
                or value.get("star_id")
                or ""
            )
            source_key = str(value.get("source_key") or value.get("key") or display_name)
        else:
            raise XingtuError(f"unsupported author input at index {index}")
        if not display_name:
            raise XingtuError(f"missing author query at index {index}")
        record = {"source_key": source_key, "display_name": display_name}
        if isinstance(value, dict):
            for field in ("item_id", "expected_author_id"):
                if value.get(field) not in (None, ""):
                    record[field] = str(value[field])
        records.append(record)
    return records


def parse_json_string(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def compact_author(author: dict[str, Any]) -> dict[str, Any]:
    attrs = author.get("attribute_datas") or {}
    last_items = parse_json_string(attrs.get("last_10_items"), [])
    representative = author.get("items") or []
    by_id = {
        str(item.get("item_id")): item
        for item in last_items
        if isinstance(item, dict) and item.get("item_id")
    }
    conflicts: list[dict[str, Any]] = []
    for item in representative:
        item_id = str(item.get("item_id") or "")
        recent = by_id.get(item_id)
        if recent and _integer(recent.get("vv")) != _integer(item.get("vv")):
            conflicts.append(
                {
                    "item_id": item_id,
                    "last_10_items_vv": _integer(recent.get("vv")),
                    "representative_items_vv": _integer(item.get("vv")),
                }
            )
    return {
        "display_name": attrs.get("nick_name", ""),
        "star_id": str(author.get("star_id") or attrs.get("id") or ""),
        "core_user_id": str(attrs.get("core_user_id") or ""),
        "followers": _integer(attrs.get("follower")),
        "city": attrs.get("city", ""),
        "gender": attrs.get("gender"),
        "tags": parse_json_string(attrs.get("tags_relation"), {}),
        "metrics": {
            "expected_play_count": _integer(attrs.get("expected_play_num")),
            "play_median_30d": _number(attrs.get("vv_median_30d")),
            "interaction_rate_30d": _number(attrs.get("interact_rate_within_30d")),
            "completion_rate_30d": _number(attrs.get("play_over_rate_within_30d")),
            "followers_change_30d": _number(attrs.get("fans_increment_within_30d")),
        },
        "pricing": {
            "video_1_20": _integer(attrs.get("price_1_20")),
            "video_21_60": _integer(attrs.get("price_20_60")),
            "video_60_plus": _integer(attrs.get("price_60")),
            "expected_cpm_1_20": _number(attrs.get("prospective_1_20_cpm")),
            "expected_cpm_21_60": _number(attrs.get("prospective_20_60_cpm")),
            "expected_cpm_60_plus": _number(attrs.get("prospective_60_cpm")),
        },
        "last_10_items": last_items,
        "representative_items": representative,
        "metric_conflicts": conflicts,
    }


def candidate_item_ids(author: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for field in ("last_10_items", "representative_items"):
        for item in author.get(field) or []:
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id") or item.get("id")
            if item_id not in (None, ""):
                values.add(str(item_id))
    return values


def _completed_author_search_requests(client: WebBridge) -> list[dict[str, Any]]:
    listed = client.call(
        "network", {"cmd": "list", "filter": "search_for_author_square"}
    )
    return [
        request
        for request in listed.get("requests") or []
        if request.get("completed")
        and request.get("status") == 200
        and "search_for_author_square" in str(request.get("url", ""))
    ]


def _poll_author_search(client: WebBridge, attempts: int = 6) -> list[dict[str, Any]]:
    for attempt in range(attempts):
        requests = _completed_author_search_requests(client)
        if requests:
            return requests
        if attempt < attempts - 1:
            time.sleep(0.75)
    return []


def _trigger_author_search_ui(client: WebBridge, keyword: str) -> bool:
    js_keyword = json.dumps(keyword, ensure_ascii=False)
    code = (
        "(()=>{"
        "const visible=e=>{const r=e.getBoundingClientRect();"
        "return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};"
        "const nodes=[...document.querySelectorAll('input,textarea,[contenteditable=\"true\"]')];"
        "const e=nodes.find(x=>visible(x)&&/搜索|达人|昵称|抖音号/.test("
        "`${x.placeholder||''} ${x.getAttribute('aria-label')||''}`))"
        "||nodes.find(x=>visible(x));"
        "if(!e)return false;"
        f"const v={js_keyword};"
        "if(e.isContentEditable){e.textContent=v;}else{"
        "const p=Object.getPrototypeOf(e);"
        "const s=Object.getOwnPropertyDescriptor(p,'value')?.set;"
        "if(s)s.call(e,v);else e.value=v;}"
        "e.dispatchEvent(new Event('input',{bubbles:true}));"
        "e.dispatchEvent(new Event('change',{bubbles:true}));"
        "e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true}));"
        "e.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',bubbles:true}));"
        "return true;"
        "})()"
    )
    return bool(client.evaluate_json(code))


def select_author_candidate(
    authors: list[dict[str, Any]], record: dict[str, str]
) -> tuple[dict[str, Any] | None, str, list[str]]:
    item_id = str(record.get("item_id") or "")
    expected_author_id = str(record.get("expected_author_id") or "")
    warnings: list[str] = []

    if item_id:
        item_matches = [
            author for author in authors if item_id in candidate_item_ids(author)
        ]
        if len(item_matches) == 1:
            selected = item_matches[0]
            if (
                expected_author_id
                and selected.get("core_user_id")
                and str(selected["core_user_id"]) != expected_author_id
            ):
                return None, "identity_conflict", ["candidate_item_author_mismatch"]
            return selected, "item_id_in_candidate_items", warnings
        if len(item_matches) > 1:
            return None, "ambiguous", ["item_id_matches_multiple_candidates"]

    if expected_author_id:
        id_matches = [
            author
            for author in authors
            if str(author.get("core_user_id") or "") == expected_author_id
        ]
        if len(id_matches) == 1:
            return id_matches[0], "author_id_core_user_id", warnings
        if len(id_matches) > 1:
            return None, "ambiguous", ["core_user_id_matches_multiple_candidates"]

    exact = [
        author
        for author in authors
        if normalize_exact(str(author.get("display_name", "")))
        == normalize_exact(record["display_name"])
    ]
    if len(exact) == 1:
        if exact[0].get("metric_conflicts"):
            warnings.append("candidate_has_unrequested_item_metric_conflicts")
        return exact[0], "exact_display_name", warnings
    if not authors:
        return None, "not_found", warnings
    return None, "ambiguous", warnings


def search_author(client: WebBridge, record: dict[str, str]) -> dict[str, Any]:
    try:
        client.call("network", {"cmd": "stop"})
    except XingtuError:
        pass
    client.call("network", {"cmd": "start"})
    filters = json.dumps(
        {"task_category": 1, "key": record["display_name"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encoded = urllib.parse.quote(urllib.parse.quote(filters, safe=""), safe="")
    url = f"{MARKET_URL}?platform_source=1&searchCategory=2&filters={encoded}"
    client.call("navigate", {"url": url, "newTab": False})
    requests = _poll_author_search(client)
    fallback_used = False
    if not requests:
        fallback_used = _trigger_author_search_ui(client, record["display_name"])
        if fallback_used:
            requests = _poll_author_search(client)
    if not requests:
        return {
            "source_key": record["source_key"],
            "query": record,
            "candidates": [],
            "status": "error",
            "errors": ["search_response_not_observed"],
            "evidence": [],
        }
    detail = client.call(
        "network", {"cmd": "detail", "requestId": requests[-1]["requestId"]}
    )
    body = detail.get("body") or {}
    authors = [compact_author(author) for author in body.get("authors") or []]
    selected_author, method_or_status, warnings = select_author_candidate(
        authors, record
    )
    if selected_author is not None:
        selected = {
            "star_id": selected_author["star_id"],
            "core_user_id": selected_author["core_user_id"],
            "match_method": method_or_status,
            "confidence": "exact",
        }
        status = "ready"
    else:
        status = method_or_status
        selected = None
    return {
        "source_key": record["source_key"],
        "query": record,
        "identity": selected,
        "candidates": authors,
        "status": status,
        "errors": [] if status == "ready" else [status],
        "warnings": warnings + (["ui_search_fallback_used"] if fallback_used else []),
        "evidence": [
            {
                "endpoint": "/gw/api/gsearch/search_for_author_square",
                "source_field": "authors",
                "observed_at": observed_at(),
            }
        ],
    }


def _candidate_play_observations(
    candidate: dict[str, Any], item_id: str
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for field in ("last_10_items", "representative_items"):
        for item in candidate.get(field) or []:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("item_id") or item.get("id") or "")
            if candidate_id != item_id:
                continue
            value = _integer(item.get("vv"))
            if value is not None:
                observations.append({"surface": field, "play_count": value})
    return observations


def published_items(
    client: WebBridge,
    records: list[dict[str, Any]],
    strict_conflicts: bool = False,
) -> list[dict[str, Any]]:
    resolve_item_urls_with_browser(client, records)
    item_results = fetch_items(client, records)
    output: list[dict[str, Any]] = []
    for source, item_result in zip(records, item_results):
        display_name = str(source.get("display_name") or source.get("name") or "")
        if item_result.get("status") not in ("ready", "partial"):
            output.append(item_result)
            continue
        if not display_name:
            item_result["status"] = "partial"
            item_result.setdefault("errors", []).append("missing_display_name")
            output.append(item_result)
            continue
        identity = item_result.get("identity") or {}
        author_result = search_author(
            client,
            {
                "source_key": str(source["source_key"]),
                "display_name": display_name,
                "item_id": str(source.get("item_id") or ""),
                "expected_author_id": str(identity.get("author_id") or ""),
            },
        )
        item_result["creator_query"] = {
            "display_name": display_name,
            "candidate_count": len(author_result.get("candidates") or []),
        }
        item_result["creator_evidence"] = author_result.get("evidence") or []
        item_result["warnings"] = author_result.get("warnings") or []
        if author_result.get("status") != "ready":
            item_result["status"] = author_result.get("status") or "error"
            item_result.setdefault("errors", []).extend(
                author_result.get("errors") or ["creator_verification_failed"]
            )
            output.append(item_result)
            continue
        selected_identity = author_result.get("identity") or {}
        selected_candidate = next(
            (
                candidate
                for candidate in author_result.get("candidates") or []
                if str(candidate.get("core_user_id") or "")
                == str(selected_identity.get("core_user_id") or "")
            ),
            None,
        )
        identity.update(
            {
                "core_user_id": str(selected_identity.get("core_user_id") or ""),
                "star_id": str(selected_identity.get("star_id") or ""),
                "match_method": (
                    "item_id+"
                    + str(selected_identity.get("match_method") or "creator_search")
                ),
                "confidence": "exact",
            }
        )
        if str(identity.get("author_id") or "") != str(
            identity.get("core_user_id") or ""
        ):
            item_result["status"] = "identity_conflict"
            item_result.setdefault("errors", []).append(
                "item_author_id_does_not_match_core_user_id"
            )
            output.append(item_result)
            continue
        observations = (
            _candidate_play_observations(
                selected_candidate, str(source.get("item_id") or "")
            )
            if selected_candidate
            else []
        )
        item_result["observations"] = observations
        detail_play = item_result.get("metrics", {}).get("play_count")
        conflicting = [
            observation
            for observation in observations
            if observation.get("play_count") != detail_play
        ]
        if conflicting:
            item_result.setdefault("warnings", []).append(
                "cached_summary_play_count_differs_from_item_detail"
            )
            item_result["metric_resolution"] = {
                "field": "play_count",
                "selected_surface": "item_detail.stats.watch_cnt",
                "reason": "item_detail_is_the_documented_current-play_source",
            }
            if strict_conflicts:
                item_result["status"] = "metric_conflict"
                item_result.setdefault("errors", []).append(
                    "play_count_differs_across_xingtu_surfaces"
                )
        output.append(item_result)
    return output


def self_test(client: WebBridge) -> dict[str, Any]:
    state = client.ensure_market()
    fields = client.fetch_json(
        "/gw/api/fe_common_service/author_options/market_fields?market_scene=1"
    )
    return {
        "status": "ready",
        "authenticated": True,
        "market_url": state.get("href"),
        "page_title": state.get("title"),
        "field_dictionary_available": bool(fields.get("data")),
        "safety_mode": "read_only",
        "observed_at": observed_at(),
    }


def ranking(client: WebBridge, args: argparse.Namespace) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "code": args.code,
            "qualifier": args.qualifier,
            "version": args.version,
            "period": args.period,
            "date": args.date,
            "limit": args.limit,
        }
    )
    body = client.fetch_json(f"{RANKING_ENDPOINT}?{query}")
    return {
        "query": {
            "code": args.code,
            "qualifier": args.qualifier,
            "version": args.version,
            "period": args.period,
            "date": args.date,
            "limit": args.limit,
        },
        "data": body.get("data") or body.get("authors") or body,
        "evidence": {
            "endpoint": RANKING_ENDPOINT,
            "observed_at": observed_at(),
        },
        "status": "ready",
    }


def task_reports(client: WebBridge, args: argparse.Namespace) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "page": args.page,
            "limit": args.limit,
            "task_name": args.task_name,
            "task_type": args.task_type,
        }
    )
    body = client.fetch_json(f"{TASK_REPORT_ENDPOINT}?{query}")
    return {
        "query": {
            "page": args.page,
            "limit": args.limit,
            "task_name": args.task_name,
            "task_type": args.task_type,
        },
        "data": body.get("data") or body,
        "evidence": {
            "endpoint": TASK_REPORT_ENDPOINT,
            "observed_at": observed_at(),
        },
        "status": "ready",
    }


def start_daemon_if_needed() -> None:
    if os.name != "nt":
        return
    executable = Path.home() / ".kimi-webbridge" / "bin" / "kimi-webbridge.exe"
    if executable.exists():
        subprocess.run(
            [str(executable), "start"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Read-only Douyin/Xingtu batch client through Kimi WebBridge."
    )
    root.add_argument("--session", default="douyin-xingtu")
    root.add_argument("--timeout", type=int, default=30)
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("self-test")

    item_cmd = commands.add_parser("items")
    item_cmd.add_argument("--input", required=True)
    item_cmd.add_argument("--output")

    published_cmd = commands.add_parser("published-items")
    published_cmd.add_argument("--input", required=True)
    published_cmd.add_argument("--output")
    published_cmd.add_argument("--strict-conflicts", action="store_true")

    author_cmd = commands.add_parser("authors")
    author_cmd.add_argument("--input", required=True)
    author_cmd.add_argument("--output")

    rank_cmd = commands.add_parser("ranking")
    rank_cmd.add_argument("--output")
    rank_cmd.add_argument("--code", default="1")
    rank_cmd.add_argument("--qualifier", default="1901")
    rank_cmd.add_argument("--version", default="flow_split")
    rank_cmd.add_argument("--period", type=int, default=30)
    rank_cmd.add_argument(
        "--date", default=(dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y%m%d")
    )
    rank_cmd.add_argument("--limit", type=int, default=100)

    report_cmd = commands.add_parser("task-reports")
    report_cmd.add_argument("--output")
    report_cmd.add_argument("--page", type=int, default=1)
    report_cmd.add_argument("--limit", type=int, default=10)
    report_cmd.add_argument("--task-name", default="")
    report_cmd.add_argument("--task-type", type=int, default=1)
    return root


def main() -> int:
    args = parser().parse_args()
    client = WebBridge(args.session, args.timeout)
    try:
        try:
            result = self_test(client)
        except XingtuError as first_error:
            if "WebBridge unavailable" not in str(first_error):
                raise
            start_daemon_if_needed()
            result = self_test(client)

        if args.command == "self-test":
            write_json(None, result)
            return 0
        if args.command == "items":
            records = item_inputs(read_json(args.input))
            resolve_item_urls_with_browser(client, records)
            write_json(args.output, fetch_items(client, records))
            return 0
        if args.command == "published-items":
            records = item_inputs(read_json(args.input))
            write_json(
                args.output,
                published_items(client, records, args.strict_conflicts),
            )
            return 0
        if args.command == "authors":
            records = author_inputs(read_json(args.input))
            write_json(
                args.output,
                [search_author(client, record) for record in records],
            )
            return 0
        if args.command == "ranking":
            write_json(args.output, ranking(client, args))
            return 0
        if args.command == "task-reports":
            write_json(args.output, task_reports(client, args))
            return 0
        raise XingtuError(f"unsupported command: {args.command}")
    except XingtuError as exc:
        print(
            json.dumps(
                {"status": "error", "errors": [str(exc)], "observed_at": observed_at()},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
