from __future__ import annotations

import argparse
import base64
import copy
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


ORIGIN = "https://www.xingtu.cn"
SEARCH_ENDPOINT = "/gw/api/gsearch/search_for_author_square"
ITEM_ENDPOINT = "/gw/api/data_sp/external_multi_get_item"
SESSION_DIR = Path(os.environ["LOCALAPPDATA"]) / "Codex" / "XingtuDirect"
SESSION_PATH = SESSION_DIR / "session.dpapi"
ENTROPY = b"Codex-XingtuDirect-v1"
SCHEMA_VERSION = 1
DROP_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "authority",
}
ALLOWED_GET_PREFIXES = (
    ITEM_ENDPOINT,
    "/gw/api/gsearch/search_intent_authors",
    "/gw/api/fe_common_service/author_options/market_fields",
    "/gw/api/gsearch/get_search_field_options",
    "/gw/api/gsearch/get_ranking_list_catalog",
    "/gw/api/gsearch/get_ranking_list_data",
    "/gw/api/gsearch/content_square_sync_date",
    "/gw/api/data_sp/project_task_report_info",
)
ALLOWED_POST_PATHS = {
    SEARCH_ENDPOINT,
    "/gw/api/gsearch/search_for_content_square",
}


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return (
        DATA_BLOB(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


def dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(ENTROPY)
    output_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Codex Xingtu direct session",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer, entropy_buffer


def dpapi_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(ENTROPY)
    output_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer, entropy_buffer


def parse_curl(raw: str) -> dict[str, Any]:
    normalized = raw.replace("\\\r\n", " ").replace("\\\n", " ")
    args = shlex.split(normalized, posix=True)
    if not args or args[0].lower() not in {"curl", "curl.exe"}:
        raise ValueError("input_is_not_curl")

    url = ""
    headers: dict[str, str] = {}
    body: bytes | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"-H", "--header"} and index + 1 < len(args):
            key, sep, value = args[index + 1].partition(":")
            normalized_key = key.strip()
            if sep and normalized_key.lower() not in DROP_HEADERS:
                headers[normalized_key] = value.strip()
            index += 2
            continue
        if arg in {"-b", "--cookie"} and index + 1 < len(args):
            headers["Cookie"] = args[index + 1]
            index += 2
            continue
        if arg in {"--data", "--data-raw", "--data-binary", "-d"} and index + 1 < len(args):
            body = args[index + 1].encode("utf-8")
            index += 2
            continue
        if arg.startswith("https://"):
            url = arg
        index += 1

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.xingtu.cn", "xingtu.cn"}:
        raise ValueError("only_xingtu_https_is_allowed")
    if parsed.path != SEARCH_ENDPOINT:
        raise ValueError("capture_search_for_author_square")
    if "Cookie" not in headers:
        raise ValueError("curl_has_no_cookie")
    if body is None:
        raise ValueError("curl_has_no_search_body")
    template = json.loads(body)
    if not isinstance(template, dict) or "page_param" not in template:
        raise ValueError("unexpected_search_body")
    headers["Accept-Encoding"] = "identity"
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "capture_url": url,
        "headers": headers,
        "search_template": template,
    }


def save_session(session: dict[str, Any]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    plaintext = json.dumps(
        session, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    protected = dpapi_protect(plaintext)
    SESSION_PATH.write_bytes(
        json.dumps(
            {
                "format": "dpapi",
                "schema_version": SCHEMA_VERSION,
                "payload": base64.b64encode(protected).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
    )


def load_session() -> dict[str, Any]:
    if not SESSION_PATH.exists():
        raise RuntimeError("auth_required:no_persisted_session")
    envelope = json.loads(SESSION_PATH.read_text(encoding="ascii"))
    protected = base64.b64decode(envelope["payload"])
    plaintext = dpapi_unprotect(protected)
    session = json.loads(plaintext)
    if session.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("auth_required:unsupported_session_schema")
    return session


def request_xingtu(
    session: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(path)
    if method == "GET":
        if not any(parsed.path.startswith(prefix) for prefix in ALLOWED_GET_PREFIXES):
            raise ValueError("GET_endpoint_not_in_read_only_allowlist")
    elif method == "POST":
        if parsed.path not in ALLOWED_POST_PATHS:
            raise ValueError("POST_endpoint_not_in_search_allowlist")
    else:
        raise ValueError("only_GET_and_search_POST_are_allowed")

    headers = {
        key: value
        for key, value in session["headers"].items()
        if key.lower() not in DROP_HEADERS
    }
    headers["Accept-Encoding"] = "identity"
    data = (
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if method == "POST"
        else None
    )
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            ORIGIN + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read())
            base = payload.get("base_resp") or {}
            code = base.get("status_code")
            if code not in (None, 0):
                message = str(base.get("status_message") or "")
                if code in (401, 403) or any(
                    marker in message.lower()
                    for marker in ("未登录", "登录失效", "请登录", "unauth", "login")
                ):
                    raise RuntimeError("auth_required:xingtu_session_expired")
                raise RuntimeError(f"xingtu_api_error:{code}")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 403):
                raise RuntimeError("auth_required:xingtu_session_expired") from exc
            if exc.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(attempt + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError(f"request_failed:{last_error}")


def build_search_payload(
    template: dict[str, Any],
    keyword: str,
    search_type: str,
    page: int,
    limit: int,
) -> dict[str, Any]:
    payload = copy.deepcopy(template)
    payload["page_param"] = {"page": str(page), "limit": str(limit)}
    payload["search_param"] = {
        "seach_type": 1 if search_type == "content" else 2,
        "keyword": keyword,
    }
    if search_type == "content":
        payload["attribute_filter"] = []
    return payload


def command_import_curl(_: argparse.Namespace) -> None:
    raw = sys.stdin.read()
    session = parse_curl(raw)
    save_session(session)
    print(
        json.dumps(
            {
                "status": "stored",
                "storage": "windows_dpapi_current_user",
                "session_path": str(SESSION_PATH),
                "created_at": session["created_at"],
            },
            ensure_ascii=False,
        )
    )


def command_status(_: argparse.Namespace) -> None:
    print(
        json.dumps(
            {
                "configured": SESSION_PATH.exists(),
                "session_path": str(SESSION_PATH),
            },
            ensure_ascii=False,
        )
    )


def command_self_test(_: argparse.Namespace) -> None:
    session = load_session()
    payload = request_xingtu(
        session,
        "POST",
        SEARCH_ENDPOINT,
        session["search_template"],
    )
    created = dt.datetime.fromisoformat(session["created_at"])
    age_hours = (
        dt.datetime.now(dt.timezone.utc) - created
    ).total_seconds() / 3600
    print(
        json.dumps(
            {
                "status": "ready",
                "author_count": len(payload.get("authors") or []),
                "session_age_hours": round(age_hours, 2),
            },
            ensure_ascii=False,
        )
    )


def command_search(args: argparse.Namespace) -> None:
    session = load_session()
    authors: list[dict[str, Any]] = []
    for page in range(1, args.pages + 1):
        payload = request_xingtu(
            session,
            "POST",
            SEARCH_ENDPOINT,
            build_search_payload(
                session["search_template"],
                args.keyword,
                args.search_type,
                page,
                args.limit,
            ),
        )
        page_authors = payload.get("authors") or []
        authors.extend(page_authors)
        if len(page_authors) < args.limit:
            break
    output = {
        "query": {
            "keyword": args.keyword,
            "search_type": args.search_type,
            "pages_requested": args.pages,
        },
        "authors": authors,
        "observed_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"output": str(target.resolve()), "authors": len(authors)},
                ensure_ascii=False,
            )
        )
    else:
        print(rendered)


def command_items(args: argparse.Namespace) -> None:
    session = load_session()
    ids = list(dict.fromkeys(args.item_id))
    items: list[dict[str, Any]] = []
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
        payload = request_xingtu(
            session,
            "GET",
            f"{ITEM_ENDPOINT}?{query}",
        )
        items.extend(payload.get("items") or [])
    output = {
        "items": items,
        "observed_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"output": str(target.resolve()), "items": len(items)},
                ensure_ascii=False,
            )
        )
    else:
        print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Direct, read-only Xingtu API client with DPAPI session storage"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    import_parser = sub.add_parser(
        "import-curl",
        help="Read Copy-as-cURL (bash) text from stdin and store it with Windows DPAPI",
    )
    import_parser.set_defaults(func=command_import_curl)

    status_parser = sub.add_parser("status")
    status_parser.set_defaults(func=command_status)

    test_parser = sub.add_parser("self-test")
    test_parser.set_defaults(func=command_self_test)

    search_parser = sub.add_parser("search")
    search_parser.add_argument("--keyword", required=True)
    search_parser.add_argument(
        "--search-type",
        choices=("content", "nickname"),
        default="content",
    )
    search_parser.add_argument("--pages", type=int, default=1)
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--output")
    search_parser.set_defaults(func=command_search)

    items_parser = sub.add_parser("items")
    items_parser.add_argument("--item-id", action="append", required=True)
    items_parser.add_argument("--output")
    items_parser.set_defaults(func=command_items)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
