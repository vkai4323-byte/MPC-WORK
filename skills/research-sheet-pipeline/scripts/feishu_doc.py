#!/usr/bin/env python3
"""Portable, credential-free Feishu document adapter for Codex agents."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


VERSION = "2.1.0"
CAPABILITIES = [
    "document.read",
    "document.copy",
    "document.update",
    "document.readback",
    "document.structure.read",
    "permission.public.read",
    "permission.public.anyone_editable",
]


class FeishuError(RuntimeError):
    pass


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_dotenv(path: Path) -> bool:
    if not path.is_file():
        return False
    values = parse_env_text(path.read_text(encoding="utf-8"))
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if values.get(key):
            os.environ.setdefault(key, values[key])
    return True


def credential_files(explicit: Optional[Path]) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        candidates.append(("explicit-env-file", explicit))
    elif os.environ.get("FEISHU_ENV_FILE"):
        candidates.append(("environment-env-file", Path(os.environ["FEISHU_ENV_FILE"])))

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    candidates.extend(
        [
            ("codex-secret-file", codex_home / "secrets" / "research-sheet-pipeline" / "feishu.env"),
            ("user-secret-file", Path.home() / ".config" / "research-sheet-pipeline" / "feishu.env"),
        ]
    )
    return candidates


def load_credentials(explicit: Optional[Path] = None) -> str:
    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        return "environment"

    seen: set[Path] = set()
    for source, raw_path in credential_files(explicit):
        path = raw_path.expanduser()
        if path in seen:
            continue
        seen.add(path)
        try:
            loaded = load_dotenv(path)
        except OSError:
            continue
        if loaded and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
            return source
    return "none"


def timeout_seconds() -> float:
    try:
        value = float(os.environ.get("FEISHU_HTTP_TIMEOUT", "30"))
    except ValueError as exc:
        raise FeishuError("FEISHU_HTTP_TIMEOUT must be a number") from exc
    if value <= 0 or value > 120:
        raise FeishuError("FEISHU_HTTP_TIMEOUT must be greater than 0 and at most 120 seconds")
    return value


def api_base() -> str:
    region = os.environ.get("FEISHU_REGION", "feishu").strip().lower()
    if region == "feishu":
        return "https://open.feishu.cn/open-apis"
    if region == "lark":
        return "https://open.larksuite.com/open-apis"
    raise FeishuError("FEISHU_REGION must be 'feishu' or 'lark'")


def redact_error_text(value: str) -> str:
    output = value
    for key in ("FEISHU_APP_SECRET", "FEISHU_APP_ID"):
        secret = os.environ.get(key)
        if secret:
            output = output.replace(secret, "<redacted>")
    return output


def request_json(
    method: str,
    path: str,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    retryable: Optional[bool] = None,
) -> dict:
    data = None
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": f"research-sheet-pipeline-feishu/{VERSION}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    if retryable is None:
        retryable = method == "GET" or path.endswith("/auth/v3/tenant_access_token/internal")
    payload = ""
    for attempt in range(2):
        request = Request(f"{api_base()}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout_seconds()) as response:
                raw = response.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise FeishuError("Feishu response exceeded 10 MiB")
                payload = raw.decode("utf-8")
            break
        except HTTPError as exc:
            error_payload = exc.read(8192).decode("utf-8", errors="replace")
            try:
                feishu_error_code = json.loads(error_payload).get("code")
            except (json.JSONDecodeError, AttributeError):
                feishu_error_code = None
            rate_limited = exc.code == 429 or feishu_error_code == 99991400
            if retryable and attempt == 0 and (rate_limited or exc.code >= 500):
                time.sleep(1.0)
                continue
            raise FeishuError(f"Feishu HTTP {exc.code}: {redact_error_text(error_payload)}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if retryable and attempt == 0:
                time.sleep(1.0)
                continue
            raise FeishuError(f"Feishu request failed: {redact_error_text(str(exc))}") from exc

    if not payload:
        return {}
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FeishuError("Feishu returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise FeishuError("Feishu returned an unexpected JSON payload")
    if result.get("code", 0) != 0:
        raise FeishuError(redact_error_text(json.dumps(result, ensure_ascii=False, indent=2)))
    return result


def get_tenant_access_token() -> str:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError(
            "Missing FEISHU_APP_ID or FEISHU_APP_SECRET. Configure them locally via environment, "
            "FEISHU_ENV_FILE, or a documented per-user secret file; never paste the secret into chat."
        )

    result = request_json(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        body={"app_id": app_id, "app_secret": app_secret},
    )
    tenant_token = result.get("tenant_access_token")
    if not isinstance(tenant_token, str) or not tenant_token:
        raise FeishuError("Feishu did not return a tenant access token")
    return tenant_token


def doctor_report(credential_source: str, auth: bool = False) -> dict:
    credentials_present = bool(os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"))
    report = {
        "version": VERSION,
        "status": "ready" if credentials_present else "needs_credentials",
        "python_ready": sys.version_info >= (3, 10),
        "auth_mode": "tenant_app",
        "region": os.environ.get("FEISHU_REGION", "feishu").strip().lower(),
        "credentials_present": credentials_present,
        "credential_source": credential_source,
        "auth_checked": auth,
        "auth_valid": None,
        "capabilities": CAPABILITIES,
        "user_action": None
        if credentials_present
        else (
            "Configure FEISHU_APP_ID and FEISHU_APP_SECRET locally. "
            "Do not paste FEISHU_APP_SECRET into chat or store it in a job manifest."
        ),
    }
    if auth and credentials_present:
        try:
            get_tenant_access_token()
        except FeishuError as exc:
            report["status"] = "auth_failed"
            report["auth_valid"] = False
            report["error"] = str(exc)
        else:
            report["auth_valid"] = True
    return report


def extract_wiki_token(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        host = (parsed.hostname or "").lower()
        if not (host == "feishu.cn" or host.endswith(".feishu.cn") or host == "larksuite.com" or host.endswith(".larksuite.com")):
            raise FeishuError("Wiki URL host must be Feishu or Lark")
        match = re.search(r"/wiki/([^/?#]+)", parsed.path)
        if not match:
            raise FeishuError("Could not find /wiki/{token} in URL")
        value = match.group(1)
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,128}", value):
        raise FeishuError("Invalid wiki token")
    return value


def encoded_token(value: object, label: str = "token") -> str:
    text = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9._~=-]{1,512}", text):
        raise FeishuError(f"Invalid {label}")
    return quote(text, safe="")


def resolve_wiki(value: str, token: str) -> dict:
    wiki_token = extract_wiki_token(value)
    result = request_json("GET", f"/wiki/v2/spaces/get_node?token={encoded_token(wiki_token, 'wiki token')}", token=token)
    node = result["data"]["node"]
    return {
        "title": node.get("title"),
        "node_token": node.get("node_token"),
        "space_id": node.get("space_id"),
        "obj_type": node.get("obj_type"),
        "obj_token": node.get("obj_token"),
    }


def raw_content(document_id: str, token: str) -> str:
    result = request_json("GET", f"/docx/v1/documents/{encoded_token(document_id, 'document id')}/raw_content", token=token)
    return result.get("data", {}).get("content", "")


def copy_wiki(
    value: str,
    title: str,
    token: str,
    target_space_id: Optional[str] = None,
    target_parent_token: Optional[str] = None,
) -> dict:
    source = resolve_wiki(value, token)
    space_id = target_space_id or source["space_id"]
    node_token = source["node_token"]
    body = {"target_space_id": str(space_id), "title": title}
    if target_parent_token:
        body["target_parent_token"] = target_parent_token
    result = request_json(
        "POST",
        f"/wiki/v2/spaces/{encoded_token(source['space_id'], 'source space id')}/nodes/{encoded_token(node_token, 'node token')}/copy",
        token=token,
        body=body,
    )
    node = result["data"]["node"]
    output = {
        "title": node.get("title") or title,
        "space_id": node.get("space_id"),
        "node_token": node.get("node_token"),
        "obj_type": node.get("obj_type"),
        "obj_token": node.get("obj_token"),
    }
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc and output["node_token"]:
        output["url"] = f"{parsed.scheme}://{parsed.netloc}/wiki/{output['node_token']}"
    return output


def root_folder_meta(token: str) -> dict:
    result = request_json("GET", "/drive/explorer/v2/root_folder/meta", token=token)
    return result.get("data", {})


def copy_docx_to_drive(document_id: str, title: str, token: str) -> dict:
    folder_token = root_folder_meta(token).get("token")
    if not folder_token:
        raise FeishuError("Root folder token was not returned by Feishu")
    result = request_json(
        "POST",
        f"/drive/v1/files/{encoded_token(document_id, 'document id')}/copy",
        token=token,
        body={"folder_token": folder_token, "name": title, "type": "docx"},
    )
    return result["data"]["file"]


def get_public_permission(file_token: str, file_type: str, token: str) -> dict:
    result = request_json(
        "GET",
        f"/drive/v2/permissions/{encoded_token(file_token, 'file token')}/public?type={encoded_token(file_type, 'file type')}",
        token=token,
    )
    return result.get("data", {}).get("permission_public", {})


def set_anyone_editable(file_token: str, file_type: str, token: str, confirm_file_token: str) -> dict:
    if confirm_file_token != file_token:
        raise FeishuError("Permission confirmation token does not match the target file token")
    before = get_public_permission(file_token, file_type, token)
    request_json(
        "PATCH",
        f"/drive/v2/permissions/{encoded_token(file_token, 'file token')}/public?type={encoded_token(file_type, 'file type')}",
        token=token,
        body={"external_access": True, "link_share_entity": "anyone_editable"},
        retryable=True,
    )
    after = get_public_permission(file_token, file_type, token)
    verified = bool(after.get("external_access") and after.get("link_share_entity") == "anyone_editable")
    if not verified:
        raise FeishuError("Permission update could not be verified by read-back")
    return {"before": before, "after": after, "verified": True}


def list_blocks(document_id: str, token: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    seen_page_tokens: set[str] = set()
    for _ in range(100):
        path = f"/docx/v1/documents/{encoded_token(document_id, 'document id')}/blocks?document_revision_id=-1&page_size=500"
        if page_token:
            path += f"&page_token={encoded_token(page_token, 'page token')}"
        result = request_json("GET", path, token=token)
        data = result.get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            return items
        next_page_token = data.get("page_token")
        if not next_page_token or next_page_token in seen_page_tokens:
            raise FeishuError("Feishu block pagination did not make progress")
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
    raise FeishuError("Feishu block pagination exceeded 100 pages")


def text_container(block: dict) -> Optional[tuple[str, dict]]:
    for key, value in block.items():
        if isinstance(value, dict) and isinstance(value.get("elements"), list):
            return key, value
    return None


def block_text(container: dict) -> str:
    parts = []
    for element in container.get("elements", []):
        text_run = element.get("text_run")
        if text_run:
            parts.append(text_run.get("content", ""))
    return "".join(parts)


def structural_signature(blocks: list[dict]) -> dict:
    index_by_id = {block.get("block_id"): index for index, block in enumerate(blocks)}
    canonical = []
    type_counts: dict[str, int] = {}
    for block in blocks:
        block_type = str(block.get("block_type", "unknown"))
        type_counts[block_type] = type_counts.get(block_type, 0) + 1
        found = text_container(block)
        container_key = found[0] if found else None
        style = found[1].get("style", {}) if found else {}
        elements = []
        if found:
            for element in found[1].get("elements", []):
                element_shape = {}
                for element_type, value in sorted(element.items()):
                    if isinstance(value, dict):
                        element_shape[element_type] = {
                            key: child
                            for key, child in value.items()
                            if not (element_type == "text_run" and key == "content")
                        }
                    else:
                        element_shape[element_type] = value
                elements.append(element_shape)
        excluded = {"block_id", "parent_id", "children", "block_type"}
        if container_key:
            excluded.add(container_key)
        canonical.append(
            {
                "type": block_type,
                "parent": index_by_id.get(block.get("parent_id"), -1),
                "children": [index_by_id.get(child, -1) for child in block.get("children", [])],
                "container": container_key,
                "style": style,
                "elements": elements,
                "anchors": sorted(key for key in block if key not in excluded),
            }
        )
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "block_count": len(blocks),
        "type_counts": type_counts,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def normalize_replacements(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        payload = [{"old": old, "new": new, "mode": "contains"} for old, new in payload.items()]
    if not isinstance(payload, list):
        raise FeishuError("Replacement map must be a JSON object or array")
    if not payload:
        raise FeishuError("Replacement map must not be empty")
    replacements: list[dict] = []
    seen_old: set[str] = set()
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("old"), str) or not isinstance(item.get("new"), str):
            raise FeishuError("Each replacement requires string old and new fields")
        old = item["old"]
        if not old:
            raise FeishuError("Replacement old value must not be empty")
        if old in seen_old:
            raise FeishuError(f"Duplicate replacement target: {old!r}")
        if any(old in existing or existing in old for existing in seen_old):
            raise FeishuError(f"Overlapping replacement targets are unsafe: {old!r}")
        mode = item.get("mode", "contains")
        if mode not in {"contains", "exact"}:
            raise FeishuError(f"Unsupported replacement mode: {mode}")
        expected_hits = item.get("expected_hits")
        if expected_hits is not None and (
            not isinstance(expected_hits, int) or isinstance(expected_hits, bool) or expected_hits < 1
        ):
            raise FeishuError("expected_hits must be a positive integer")
        replacements.append({"old": old, "new": item["new"], "mode": mode, "expected_hits": expected_hits})
        seen_old.add(old)
    return replacements


def replace_text_map(document_id: str, replacements: list[dict], token: str, dry_run: bool = False) -> dict:
    blocks = list_blocks(document_id, token)
    signature_before = structural_signature(blocks)
    hit_counts = {item["old"]: 0 for item in replacements}
    plans = []

    for block in blocks:
        found = text_container(block)
        if not found:
            continue
        _, container = found
        original = block_text(container)
        if not original:
            continue

        elements = json.loads(json.dumps(container["elements"], ensure_ascii=False))
        active = [
            item
            for item in replacements
            if (item["mode"] == "exact" and original == item["old"])
            or (item["mode"] == "contains" and item["old"] in original)
        ]
        if not active:
            continue

        matched = []
        for item in active:
            old = item["old"]
            logical_hits = original.count(old)
            run_hits = sum(
                element.get("text_run", {}).get("content", "").count(old)
                for element in container["elements"]
            )
            if run_hits != logical_hits:
                raise FeishuError(f"Replacement spans styled elements and is unsafe: {old!r}")
            hit_counts[old] += run_hits
            matched.append(old)

        replacements_by_old = {item["old"]: item["new"] for item in active}
        pattern = re.compile("|".join(re.escape(old) for old in sorted(replacements_by_old, key=len, reverse=True)))
        for element in elements:
            text_run = element.get("text_run")
            if not text_run:
                continue
            content = text_run.get("content", "")
            text_run["content"] = pattern.sub(lambda match: replacements_by_old[match.group(0)], content)
        current = block_text({"elements": elements})

        if matched:
            plans.append(
                {
                    "block_id": block["block_id"],
                    "before": original,
                    "after": current,
                    "matched": matched,
                    "elements": elements,
                }
            )

    unmatched = [old for old, count in hit_counts.items() if count == 0]
    expected = {item["old"]: item.get("expected_hits") for item in replacements}
    unexpected_hits = {
        old: {"expected": expected[old], "actual": count}
        for old, count in hit_counts.items()
        if expected[old] is not None and count != expected[old]
    }
    report = {
        "document_id": document_id,
        "dry_run": dry_run,
        "planned_blocks": len(plans),
        "applied_blocks": 0,
        "apply_status": "dry_run" if dry_run else "not_applied",
        "hit_counts": hit_counts,
        "unmatched": unmatched,
        "unexpected_hits": unexpected_hits,
        "readback_mismatches": [],
        "readback_verified": None,
        "structure_verified": None,
        "verification_status": "not_run",
        "changes": [
            {key: plan[key] for key in ("block_id", "before", "after", "matched")}
            for plan in plans
        ],
    }
    if unmatched or unexpected_hits:
        return report

    if not dry_run:
        if len(plans) > 200:
            raise FeishuError("Replacement plan exceeds the Feishu batch limit of 200 blocks; no changes were applied")
        client_token = str(uuid.uuid4())
        requests = [
            {
                "block_id": plan["block_id"],
                "update_text_elements": {"elements": plan["elements"]},
            }
            for plan in plans
        ]
        if requests:
            try:
                request_json(
                    "PATCH",
                    (
                        f"/docx/v1/documents/{encoded_token(document_id, 'document id')}/blocks/batch_update"
                        f"?document_revision_id=-1&client_token={encoded_token(client_token, 'client token')}"
                    ),
                    token=token,
                    body={"requests": requests},
                    retryable=True,
                )
            except FeishuError as exc:
                report["apply_status"] = "unknown"
                report["apply_error"] = str(exc)
                return report
        report["applied_blocks"] = len(plans)
        report["apply_status"] = "applied"

        try:
            blocks_after = list_blocks(document_id, token)
        except FeishuError as exc:
            report["verification_status"] = "failed"
            report["readback_error"] = str(exc)
            return report
        blocks_after_by_id = {block.get("block_id"): block for block in blocks_after}
        for plan in plans:
            updated_block = blocks_after_by_id.get(plan["block_id"])
            found = text_container(updated_block) if updated_block else None
            actual = block_text(found[1]) if found else None
            if actual != plan["after"]:
                report["readback_mismatches"].append(plan["block_id"])
        report["readback_verified"] = not report["readback_mismatches"]
        report["structure_verified"] = signature_before == structural_signature(blocks_after)
        report["verification_status"] = (
            "verified"
            if report["readback_verified"] and report["structure_verified"]
            else "failed"
        )
    return report


def append_text(document_id: str, parent_block_id: str, text: str, token: str, index: int = -1) -> dict:
    body = {
        "index": index,
        "children": [
            {
                "block_type": 2,
                "text": {
                    "elements": [{"text_run": {"content": text, "text_element_style": {}}}],
                    "style": {},
                },
            }
        ],
    }
    result = request_json(
        "POST",
        f"/docx/v1/documents/{encoded_token(document_id, 'document id')}/blocks/{encoded_token(parent_block_id, 'parent block id')}/children?document_revision_id=-1",
        token=token,
        body=body,
    )
    return result["data"]["children"][0]


def replacement_failed(report: dict, dry_run: bool) -> bool:
    return (
        bool(report["unmatched"])
        or bool(report["unexpected_hits"])
        or bool(report["readback_mismatches"])
        or report["structure_verified"] is False
        or (not dry_run and report["apply_status"] != "applied")
        or (not dry_run and report["verification_status"] != "verified")
    )


def self_test() -> None:
    assert parse_env_text("A=1\nFEISHU_APP_ID='x'") == {"A": "1", "FEISHU_APP_ID": "x"}
    assert normalize_replacements({"old": "new"})[0]["mode"] == "contains"
    for payload in (
        [],
        [{"old": "", "new": "x"}],
        [{"old": "a", "new": "x"}, {"old": "a", "new": "y"}],
        [{"old": "a", "new": "x", "mode": "unknown"}],
        [{"old": "a", "new": "x", "expected_hits": True}],
    ):
        try:
            normalize_replacements(payload)
        except FeishuError:
            pass
        else:
            raise AssertionError(f"unsafe replacement payload was accepted: {payload!r}")
    signature = structural_signature([{"block_id": "root", "block_type": 1, "children": []}])
    assert signature["block_count"] == 1 and len(signature["sha256"]) == 64
    styled_a = structural_signature(
        [{"block_id": "x", "block_type": 2, "text": {"elements": [{"text_run": {"content": "one", "text_element_style": {"bold": True}}}]}}]
    )
    styled_b = structural_signature(
        [{"block_id": "x", "block_type": 2, "text": {"elements": [{"text_run": {"content": "two", "text_element_style": {"bold": True}}}]}}]
    )
    unstyled = structural_signature(
        [{"block_id": "x", "block_type": 2, "text": {"elements": [{"text_run": {"content": "one", "text_element_style": {}}}]}}]
    )
    assert styled_a["sha256"] == styled_b["sha256"]
    assert styled_a["sha256"] != unstyled["sha256"]
    status_report = {
        "unmatched": [],
        "unexpected_hits": {},
        "readback_mismatches": [],
        "structure_verified": None,
        "apply_status": "unknown",
        "verification_status": "not_run",
    }
    assert replacement_failed(status_report, dry_run=False)
    status_report.update({"apply_status": "applied", "verification_status": "failed"})
    assert replacement_failed(status_report, dry_run=False)
    status_report.update({"structure_verified": True, "verification_status": "verified"})
    assert not replacement_failed(status_report, dry_run=False)
    try:
        set_anyone_editable("target", "docx", "unused", "different")
    except FeishuError:
        pass
    else:
        raise AssertionError("permission confirmation mismatch was accepted")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable Feishu docs helper for research-sheet-pipeline.")
    parser.add_argument("--env-file", type=Path, help="Local credential file. Never commit or share it.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("self-test", help="Run offline safety and schema checks.")

    doctor = sub.add_parser("doctor", help="Report capabilities and credential readiness without exposing values.")
    doctor.add_argument("--auth", action="store_true", help="Also validate credentials with a token request.")

    resolve = sub.add_parser("resolve", help="Resolve a Feishu wiki URL/token.")
    resolve.add_argument("wiki")
    raw = sub.add_parser("raw", help="Print raw text from a docx document.")
    raw.add_argument("document_id")
    copy = sub.add_parser("wiki-copy", help="Copy a wiki node in its current knowledge space.")
    copy.add_argument("wiki")
    copy.add_argument("title")
    copy.add_argument("--target-space-id")
    copy.add_argument("--target-parent-token")
    drive_copy = sub.add_parser("drive-copy", help="Copy a docx to the app's writable drive root.")
    drive_copy.add_argument("document_id")
    drive_copy.add_argument("title")
    blocks = sub.add_parser("blocks", help="Print all document blocks as JSON.")
    blocks.add_argument("document_id")
    signature = sub.add_parser("signature", help="Print a compact structural signature.")
    signature.add_argument("document_id")
    permission_get = sub.add_parser("permission-get", help="Get public sharing settings.")
    permission_get.add_argument("file_token")
    permission_get.add_argument("--type", default="docx", dest="file_type")
    permission_edit = sub.add_parser("permission-anyone-edit", help="Allow anyone with the link to edit after explicit target confirmation.")
    permission_edit.add_argument("file_token")
    permission_edit.add_argument("--type", default="docx", dest="file_type")
    permission_edit.add_argument("--confirm-file-token", required=True)
    replace = sub.add_parser("replace-map", help="Replace text from a JSON map file.")
    replace.add_argument("document_id")
    replace.add_argument("map_file")
    replace.add_argument("--dry-run", action="store_true")
    append = sub.add_parser("append", help="Append one paragraph to a docx document.")
    append.add_argument("document_id")
    append.add_argument("text")
    append.add_argument("--parent-block-id", default=None)
    append.add_argument("--index", type=int, default=-1)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "self-test":
        self_test()
        print("self-test passed")
        return 0
    credential_source = load_credentials(args.env_file)
    if args.command == "doctor":
        print(json.dumps(doctor_report(credential_source, auth=args.auth), ensure_ascii=False, indent=2))
        return 0

    token = get_tenant_access_token()
    if args.command == "resolve":
        output = resolve_wiki(args.wiki, token)
    elif args.command == "raw":
        print(raw_content(args.document_id, token))
        return 0
    elif args.command == "wiki-copy":
        output = copy_wiki(
            args.wiki,
            args.title,
            token,
            target_space_id=args.target_space_id,
            target_parent_token=args.target_parent_token,
        )
    elif args.command == "drive-copy":
        output = copy_docx_to_drive(args.document_id, args.title, token)
    elif args.command == "blocks":
        output = list_blocks(args.document_id, token)
    elif args.command == "signature":
        output = structural_signature(list_blocks(args.document_id, token))
    elif args.command == "permission-get":
        output = get_public_permission(args.file_token, args.file_type, token)
    elif args.command == "permission-anyone-edit":
        output = set_anyone_editable(args.file_token, args.file_type, token, args.confirm_file_token)
    elif args.command == "replace-map":
        map_path = Path(args.map_file)
        replacements = normalize_replacements(json.loads(map_path.read_text(encoding="utf-8")))
        output = replace_text_map(args.document_id, replacements, token, dry_run=args.dry_run)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        failed = replacement_failed(output, args.dry_run)
        return 2 if failed else 0
    elif args.command == "append":
        parent = args.parent_block_id or args.document_id
        output = append_text(args.document_id, parent, args.text, token, index=args.index)
    else:
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FeishuError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
