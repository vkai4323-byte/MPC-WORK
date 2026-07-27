#!/usr/bin/env python3
"""Safely write resumable checkpoints and validate bounded run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "secret",
    "client_secret",
    "api_key",
    "private_key",
    "credential",
    "credentials",
    "password",
    "passwd",
    "cookie",
    "set_cookie",
    "authorization",
    "bearer_token",
    "token",
    "id_token",
    "auth_token",
    "access_token",
    "refresh_token",
    "disposable_login_token",
    "email",
    "session_id",
    "collection_id",
}
MINIMAL_ALLOWLIST = {
    "checkpoint.json",
    "change-plan.json",
    "verification-summary.json",
}
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)
AUTH_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization)\s*:\s*[^\r\n]+"
)
COOKIE_HEADER_RE = re.compile(
    r"(?im)\b(cookie|set-cookie)\s*:\s*[^\r\n]+"
)
PRIVATE_KEY_RE = re.compile(
    (
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z0-9 ]*PRIVATE KEY-----"
    ),
    re.DOTALL,
)
BEARER_VALUE_RE = re.compile(
    r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    (
        r"(?i)\b([A-Za-z0-9_.-]*(?:token|secret|password|passwd|"
        r"api[_-]?key|private[_-]?key|authorization|cookie|credential|"
        r"session[_-]?id|collection[_-]?id)[A-Za-z0-9_.-]*)"
        r"\s*([:=])\s*([^\s,;&]+)"
    )
)
TEXT_EXTENSIONS = {
    ".csv",
    ".html",
    ".json",
    ".log",
    ".md",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MAX_TEXT_SCAN_BYTES = 3 * 1024 * 1024
REQUIRED_STATE_FIELDS = {
    "checkpoint_status",
    "contract_hash",
    "skill_snapshot",
    "adapter_versions",
    "source_snapshot",
    "stage_state",
    "attempts",
    "frozen_source_keys",
    "records",
    "destination_state",
    "verified_outputs",
    "resume_from",
    "checkpoint_hash",
    "unexplained_mismatches",
}


class ArtifactError(ValueError):
    """A user-correctable checkpoint or artifact-policy error."""


def _normalized_key(value: Any) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value).strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _is_sensitive_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    padded = f"_{normalized}_"
    return any(
        f"_{token}_" in padded
        for token in SENSITIVE_KEYS
    )


def _scrub_url(url: str) -> tuple[str, int]:
    """Redact sensitive query values in one absolute HTTP(S) URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, 0
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return url, 0

    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return url, 0

    redacted_count = 0
    cleaned_pairs: list[tuple[str, str]] = []
    for key, value in pairs:
        if _is_sensitive_key(key):
            cleaned_pairs.append((key, REDACTED))
            if value != REDACTED:
                redacted_count += 1
        else:
            cleaned_pairs.append((key, value))
    if not redacted_count:
        return url, 0

    cleaned_query = urlencode(cleaned_pairs, doseq=True)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, cleaned_query, parts.fragment)
    ), redacted_count


def _scrub_urls_in_text(value: str) -> tuple[str, int]:
    """Scrub every HTTP(S) URL found in a string."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        cleaned, redacted = _scrub_url(match.group(0))
        count += redacted
        return cleaned

    return URL_RE.sub(replace, value), count


def _scrub_free_text(value: str) -> tuple[str, int]:
    count = 0

    def header_replace(match: re.Match[str]) -> str:
        nonlocal count
        if REDACTED in match.group(0):
            return match.group(0)
        count += 1
        return f"{match.group(1)}: {REDACTED}"

    cleaned = AUTH_HEADER_RE.sub(header_replace, value)
    cleaned = COOKIE_HEADER_RE.sub(header_replace, cleaned)
    cleaned, private_key_count = PRIVATE_KEY_RE.subn(REDACTED, cleaned)
    count += private_key_count
    cleaned, bearer_count = BEARER_VALUE_RE.subn(REDACTED, cleaned)
    count += bearer_count

    def assignment_replace(match: re.Match[str]) -> str:
        nonlocal count
        if not _is_sensitive_key(match.group(1)):
            return match.group(0)
        if match.group(3) == REDACTED:
            return match.group(0)
        count += 1
        return f"{match.group(1)}{match.group(2)}{REDACTED}"

    cleaned = SENSITIVE_ASSIGNMENT_RE.sub(assignment_replace, cleaned)
    return cleaned, count


def redact_sensitive(value: Any) -> tuple[Any, dict[str, int]]:
    """Return a recursively redacted JSON-compatible value and statistics."""
    stats = {
        "sensitive_fields": 0,
        "url_query_values": 0,
        "email_values": 0,
        "free_text_secrets": 0,
    }

    def walk(
        item: Any,
        field_name: str = "",
        path: tuple[str, ...] = (),
    ) -> Any:
        if isinstance(item, dict):
            cleaned: dict[Any, Any] = {}
            for key, child in item.items():
                normalized_key = _normalized_key(key)
                if _is_sensitive_key(key):
                    cleaned[key] = REDACTED
                    if child != REDACTED:
                        stats["sensitive_fields"] += 1
                else:
                    cleaned[key] = walk(
                        child,
                        normalized_key,
                        path + (normalized_key,),
                    )
            return cleaned
        if isinstance(item, list):
            return [
                walk(child, field_name, path)
                for child in item
            ]
        if isinstance(item, str):
            cleaned, count = _scrub_urls_in_text(item)
            stats["url_query_values"] += count
            cleaned, email_count = EMAIL_RE.subn(REDACTED, cleaned)
            stats["email_values"] += email_count
            cleaned, text_count = _scrub_free_text(cleaned)
            stats["free_text_secrets"] += text_count
            protected_exact_key = (
                field_name in {
                    "source_key",
                    "frozen_source_keys",
                    "entities",
                }
                or (
                    field_name == "key"
                    and "entities" in path
                )
            )
            if protected_exact_key and cleaned != item:
                raise ArtifactError(
                    "sensitive exact key cannot be safely persisted; "
                    "use an approved opaque exact key or a non-resumable run"
                )
            return cleaned
        return item

    return walk(value), stats


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_checkpoint_envelope(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "3.0":
        raise ArtifactError("checkpoint requires schema_version=3.0")
    if not isinstance(payload.get("job_id"), str) or not payload["job_id"].strip():
        raise ArtifactError("checkpoint requires a non-empty job_id")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ArtifactError("checkpoint requires a state object")
    if state.get("checkpoint_status") not in {"new", "resuming"}:
        raise ArtifactError(
            "checkpoint state.checkpoint_status must be new or resuming"
        )
    missing = sorted(REQUIRED_STATE_FIELDS - set(state))
    if missing:
        raise ArtifactError(
            "checkpoint state is missing required fields: "
            + ", ".join(missing)
        )


def _validate_with_compose(payload: dict[str, Any]) -> None:
    try:
        from compose_chain import compose
    except ImportError as exc:
        raise ArtifactError(
            "compose_chain.py is required beside checkpoint_artifacts.py"
        ) from exc
    result = compose(payload)
    if not result.get("valid"):
        messages = result.get("errors") or ["unknown manifest validation error"]
        raise ArtifactError(
            "checkpoint manifest failed compose validation: "
            + "; ".join(str(message) for message in messages[:8])
        )


def _set_checkpoint_hashes(payload: dict[str, Any]) -> dict[str, str]:
    """Set v3 contract and checkpoint hashes using compose_chain semantics."""
    _validate_checkpoint_envelope(payload)
    state = payload.get("state")
    assert isinstance(state, dict)

    contract_payload = {
        key: value for key, value in payload.items() if key != "state"
    }
    state["contract_hash"] = _canonical_hash(contract_payload)
    state["checkpoint_hash"] = ""
    state["checkpoint_hash"] = _canonical_hash(payload)
    return {
        "contract_hash": state["contract_hash"],
        "checkpoint_hash": state["checkpoint_hash"],
    }


def atomic_write_json(output: Path, payload: Any) -> None:
    """Atomically write JSON using a temporary sibling and os.replace."""
    if output.name != "checkpoint.json":
        raise ArtifactError("output filename must be exactly checkpoint.json")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".checkpoint-",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def _read_json_input(source: str) -> Any:
    if source == "-":
        return json.load(sys.stdin)
    with Path(source).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_checkpoint(input_source: str, output: Path) -> dict[str, Any]:
    payload = _read_json_input(input_source)
    if not isinstance(payload, dict):
        raise ArtifactError("checkpoint input must be a JSON object")
    cleaned, redaction_stats = redact_sensitive(payload)
    _validate_checkpoint_envelope(cleaned)
    hashes = _set_checkpoint_hashes(cleaned)
    _validate_with_compose(cleaned)
    atomic_write_json(output, cleaned)
    return {
        "ok": True,
        "operation": "write-checkpoint",
        "output": str(output.resolve()),
        "redactions": redaction_stats,
        "hashes": hashes,
    }


def _redaction_count(stats: dict[str, int]) -> int:
    return sum(stats.values())


def _scan_text_artifact(path: Path) -> tuple[int, str | None]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return 0, f"could not stat text artifact: {exc}"
    if size > MAX_TEXT_SCAN_BYTES:
        return 0, (
            "text artifact exceeds the bounded sensitive-content scan limit "
            f"of {MAX_TEXT_SCAN_BYTES} bytes"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, f"could not decode text artifact as UTF-8: {exc}"

    if path.suffix.lower() == ".json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = text
    else:
        value = text
    try:
        _, stats = redact_sensitive(value)
    except ArtifactError as exc:
        return 1, str(exc)
    return _redaction_count(stats), None


def _file_details(
    path: Path, artifact_dir: Path, now: float, retention_days: int
) -> dict[str, Any]:
    stat = path.stat()
    age_seconds = max(0.0, now - stat.st_mtime)
    return {
        "path": path.relative_to(artifact_dir).as_posix(),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat(),
        "age_days": round(age_seconds / 86400.0, 3),
        "stale": age_seconds > retention_days * 86400,
    }


def verify_artifact_dir(
    artifact_dir: Path,
    mode: str,
    max_debug_files: int,
    debug_retention_days: int,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    if mode not in {"minimal", "debug_on_failure", "debug"}:
        raise ArtifactError(f"unsupported artifact mode: {mode}")
    if not 1 <= max_debug_files <= 50:
        raise ArtifactError("max-debug-files must be between 1 and 50")
    if not 1 <= debug_retention_days <= 30:
        raise ArtifactError(
            "debug-retention-days must be between 1 and 30"
        )

    result: dict[str, Any] = {
        "ok": False,
        "operation": "verify-dir",
        "artifact_dir": str(artifact_dir.resolve()),
        "mode": mode,
        "policy": {
            "minimal_allowlist": sorted(MINIMAL_ALLOWLIST),
            "max_debug_files": max_debug_files,
            "debug_retention_days": debug_retention_days,
            "deletes_files": False,
        },
        "files": [],
        "extra_files": [],
        "sensitive_files": [],
        "violations": [],
    }
    violations: list[str] = result["violations"]

    if not artifact_dir.exists():
        violations.append("artifact directory does not exist")
        return result
    if not artifact_dir.is_dir():
        violations.append("artifact path is not a directory")
        return result

    observed_at = time.time() if now is None else now
    files: list[Path] = []
    try:
        for candidate in sorted(
            artifact_dir.rglob("*"),
            key=lambda item: item.relative_to(artifact_dir).as_posix(),
        ):
            if candidate.is_symlink():
                violations.append(
                    "symbolic links are not allowed: "
                    + candidate.relative_to(artifact_dir).as_posix()
                )
                continue
            if candidate.is_file():
                files.append(candidate)
    except OSError as exc:
        violations.append(f"failed to enumerate artifact directory: {exc}")
        return result

    details = [
        _file_details(path, artifact_dir, observed_at, debug_retention_days)
        for path in files
    ]
    result["files"] = details
    extras = [
        item for item in details if item["path"] not in MINIMAL_ALLOWLIST
    ]
    result["extra_files"] = extras

    sensitive_files: list[dict[str, Any]] = result["sensitive_files"]
    for path in files:
        relative = path.relative_to(artifact_dir).as_posix()
        if (
            path.suffix.lower() not in TEXT_EXTENSIONS
            and relative not in MINIMAL_ALLOWLIST
        ):
            continue
        count, scan_error = _scan_text_artifact(path)
        if scan_error:
            violations.append(
                f"artifact content could not be verified: {relative}: {scan_error}"
            )
            continue
        if count:
            sensitive_files.append(
                {"path": relative, "redaction_candidates": count}
            )
            violations.append(
                f"sensitive content detected in artifact: {relative}"
            )

    checkpoint_path = artifact_dir / "checkpoint.json"
    if checkpoint_path.is_file():
        try:
            checkpoint_payload = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if not isinstance(checkpoint_payload, dict):
                raise ArtifactError(
                    "checkpoint.json must contain a JSON object"
                )
            _validate_checkpoint_envelope(checkpoint_payload)
            _validate_with_compose(checkpoint_payload)
        except (
            ArtifactError,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
        ) as exc:
            violations.append(f"invalid checkpoint.json: {exc}")

    if mode == "minimal":
        for item in extras:
            violations.append(
                f"file is not allowed in minimal mode: {item['path']}"
            )
    else:
        if len(extras) > max_debug_files:
            violations.append(
                "debug file count exceeds limit: "
                f"{len(extras)} > {max_debug_files}"
            )
        for item in extras:
            if item["stale"]:
                violations.append(
                    "debug file exceeds retention: "
                    f"{item['path']} ({item['age_days']} days)"
                )

    result["ok"] = not violations
    return result


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def self_test() -> dict[str, Any]:
    tests: list[str] = []
    with tempfile.TemporaryDirectory(prefix="checkpoint-artifacts-test-") as tmp:
        root = Path(tmp)
        source = root / "source.json"
        source.write_text(
            json.dumps(
                {
                    "schema_version": "3.0",
                    "job_id": "test-job",
                    "objective": "checkpoint helper self-test",
                    "scope": {},
                    "entities": [],
                    "sheet": {"mode": "none"},
                    "research": {"enabled": False},
                    "documents": {"mode": "none"},
                    "writeback": {"mode": "none"},
                    "verification": {},
                    "execution": {
                        "resume": "checkpoint",
                        "redact_sensitive": True,
                    },
                    "state": {
                        "checkpoint_status": "new",
                        "contract_hash": "",
                        "skill_snapshot": {"commit": "", "hash": ""},
                        "adapter_versions": {},
                        "source_snapshot": {
                            "ref": "",
                            "hash": "",
                            "observed_at": "",
                        },
                        "stage_state": {},
                        "attempts": {},
                        "frozen_source_keys": [],
                        "records": [],
                        "destination_state": {},
                        "verified_outputs": {},
                        "resume_from": "",
                        "checkpoint_hash": "",
                        "unexplained_mismatches": [],
                    },
                    "password": "plain-text",
                    "nested": {
                        "clientSecret": "secret",
                        "Access-Token": "token",
                        "safe": "keep",
                    },
                    "contact": "owner@example.test",
                    "notes": (
                        "Authorization: Bearer abc123\n"
                        "Cookie: sid=unsafe\nprivate_key=unsafe"
                    ),
                    "note2": "Bearer abc12345",
                    "url": (
                        "https://example.test/path?session_id=abc"
                        "&token=unsafe&page=2"
                        "&email=user%40example.test#section"
                    ),
                }
            ),
            encoding="utf-8",
        )
        artifact_dir = root / "run"
        checkpoint = artifact_dir / "checkpoint.json"
        write_result = write_checkpoint(str(source), checkpoint)
        written = json.loads(checkpoint.read_text(encoding="utf-8"))
        _assert(write_result["ok"], "write-checkpoint did not succeed")
        _assert(written["password"] == REDACTED, "password was not redacted")
        _assert(
            written["nested"]["Access-Token"] == REDACTED,
            "case-insensitive sensitive key was not redacted",
        )
        _assert(
            written["nested"]["clientSecret"] == REDACTED,
            "camel-case composite sensitive key was not redacted",
        )
        _assert(written["nested"]["safe"] == "keep", "safe value changed")
        _assert(
            written["contact"] == REDACTED,
            "email value outside an email-key field was not redacted",
        )
        _assert(
            written["state"]["contract_hash"].startswith("sha256:"),
            "contract hash was not populated",
        )
        expected_checkpoint_hash = written["state"]["checkpoint_hash"]
        written["state"]["checkpoint_hash"] = ""
        _assert(
            expected_checkpoint_hash == _canonical_hash(written),
            "checkpoint integrity hash is not canonical",
        )
        written["state"]["checkpoint_hash"] = expected_checkpoint_hash
        _assert("abc" not in written["url"], "URL session_id was not redacted")
        _assert(
            "user%40example.test" not in written["url"],
            "URL email was not redacted",
        )
        _assert("page=2" in written["url"], "safe URL query value changed")
        _assert(
            "abc123" not in written["notes"]
            and "sid=unsafe" not in written["notes"]
            and "private_key=unsafe" not in written["notes"],
            "free-text secret was not redacted",
        )
        _assert(
            written["note2"] == REDACTED,
            "standalone bearer credential was not redacted",
        )
        tests.append("recursive and URL redaction")
        tests.append("atomic checkpoint write")

        sensitive_key_shapes = [
            {"source_key": "owner@example.test"},
            {"entities": ["owner@example.test"]},
            {"entities": [{"key": "owner@example.test"}]},
            {"state": {"frozen_source_keys": ["owner@example.test"]}},
        ]
        for sensitive_shape in sensitive_key_shapes:
            try:
                redact_sensitive(sensitive_shape)
            except ArtifactError:
                continue
            raise AssertionError(
                "sensitive exact key was silently rewritten"
            )
        tests.append("sensitive exact-key shapes fail closed")

        invalid_source = root / "invalid-v2.json"
        invalid_source.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "job_id": "invalid",
                    "state": {"checkpoint_status": "resuming"},
                }
            ),
            encoding="utf-8",
        )
        try:
            write_checkpoint(
                str(invalid_source),
                root / "invalid-run" / "checkpoint.json",
            )
        except ArtifactError:
            tests.append("invalid checkpoint fail closed")
        else:
            raise AssertionError("invalid v2 checkpoint was written")

        try:
            atomic_write_json(artifact_dir / "other.json", {})
        except ArtifactError:
            tests.append("checkpoint filename enforcement")
        else:
            raise AssertionError("invalid checkpoint filename was accepted")

        minimal = verify_artifact_dir(
            artifact_dir, "minimal", max_debug_files=1, debug_retention_days=1
        )
        _assert(minimal["ok"], "valid minimal directory was rejected")
        tests.append("minimal allowlist accepts bounded artifacts")

        unsafe_plan = artifact_dir / "change-plan.json"
        unsafe_plan.write_text(
            json.dumps({"password": "unsafe"}),
            encoding="utf-8",
        )
        sensitive_plan = verify_artifact_dir(
            artifact_dir, "minimal", max_debug_files=1, debug_retention_days=1
        )
        _assert(
            not sensitive_plan["ok"]
            and sensitive_plan["sensitive_files"],
            "allowlisted sensitive content was not rejected",
        )
        unsafe_plan.write_text(
            json.dumps({"summary": "safe"}),
            encoding="utf-8",
        )
        tests.append("allowlisted content sensitive scan")

        debug_file = artifact_dir / "trace.txt"
        debug_file.write_text("debug", encoding="utf-8")
        minimal_extra = verify_artifact_dir(
            artifact_dir, "minimal", max_debug_files=1, debug_retention_days=1
        )
        _assert(
            not minimal_extra["ok"],
            "minimal mode did not reject an extra file",
        )
        tests.append("minimal allowlist rejects extras")

        debug_ok = verify_artifact_dir(
            artifact_dir, "debug", max_debug_files=1, debug_retention_days=1
        )
        _assert(debug_ok["ok"], "bounded fresh debug file was rejected")
        debug_file.write_text(
            "Cookie: sid=unsafe",
            encoding="utf-8",
        )
        sensitive_debug = verify_artifact_dir(
            artifact_dir, "debug", max_debug_files=1, debug_retention_days=1
        )
        _assert(
            not sensitive_debug["ok"]
            and sensitive_debug["sensitive_files"],
            "debug sensitive content was not rejected",
        )
        debug_file.write_text("debug", encoding="utf-8")
        tests.append("debug content sensitive scan")
        old = time.time() - 3 * 86400
        os.utime(debug_file, (old, old))
        debug_stale = verify_artifact_dir(
            artifact_dir,
            "debug_on_failure",
            max_debug_files=1,
            debug_retention_days=1,
        )
        _assert(
            not debug_stale["ok"],
            "stale debug file was not reported",
        )
        _assert(debug_file.exists(), "verification deleted a debug file")
        tests.append("debug count and retention validation")
        tests.append("verification is non-destructive")

    return {
        "ok": True,
        "operation": "self-test",
        "tests_passed": len(tests),
        "tests": tests,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically write redacted checkpoints and verify bounded artifacts."
        )
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the built-in standard-library test suite",
    )
    subparsers = parser.add_subparsers(dest="command")

    writer = subparsers.add_parser(
        "write-checkpoint",
        help="redact and atomically write checkpoint.json",
    )
    writer.add_argument(
        "--input",
        required=True,
        help="input JSON file, or - for stdin",
    )
    writer.add_argument("--output", required=True, type=Path)

    verifier = subparsers.add_parser(
        "verify-dir",
        help="verify an artifact directory without deleting files",
    )
    verifier.add_argument("--artifact-dir", required=True, type=Path)
    verifier.add_argument(
        "--mode",
        required=True,
        choices=("minimal", "debug_on_failure", "debug"),
    )
    verifier.add_argument("--max-debug-files", type=int, default=20)
    verifier.add_argument("--debug-retention-days", type=int, default=7)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            if args.command:
                raise ArtifactError(
                    "--self-test cannot be combined with a subcommand"
                )
            _print_json(self_test())
            return 0
        if args.command == "write-checkpoint":
            _print_json(write_checkpoint(args.input, args.output))
            return 0
        if args.command == "verify-dir":
            result = verify_artifact_dir(
                args.artifact_dir,
                args.mode,
                args.max_debug_files,
                args.debug_retention_days,
            )
            _print_json(result)
            return 0 if result["ok"] else 2
        raise ArtifactError(
            "choose --self-test, write-checkpoint, or verify-dir"
        )
    except (ArtifactError, json.JSONDecodeError, OSError) as exc:
        _print_json(
            {
                "ok": False,
                "operation": getattr(args, "command", None) or "startup",
                "violations": [str(exc)],
            }
        )
        return 2
    except AssertionError as exc:
        _print_json(
            {
                "ok": False,
                "operation": "self-test",
                "violations": [str(exc)],
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
