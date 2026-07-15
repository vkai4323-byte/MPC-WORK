#!/usr/bin/env python3
"""Resolve a portable document provider without exposing credentials."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


BUNDLED_CAPABILITIES = [
    "document.read",
    "document.copy",
    "document.update",
    "document.readback",
    "document.structure.read",
    "permission.public.read",
    "permission.public.anyone_editable",
]
CONFIG_ENV = "RESEARCH_SHEET_PIPELINE_CONFIG"
CLI_ENV = "FEISHU_DOC_CLI"
PROVIDER_ENV = "RESEARCH_SHEET_DOCUMENT_PROVIDER"


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def default_config_paths(explicit: Optional[Path] = None) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if explicit:
        return [("explicit-config", explicit)]
    elif os.environ.get(CONFIG_ENV):
        return [("environment-config", Path(os.environ[CONFIG_ENV]))]

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    candidates.extend(
        [
            ("codex-home-config", codex_home / "research-sheet-pipeline.local.json"),
            ("user-config", Path.home() / ".config" / "research-sheet-pipeline" / "config.json"),
        ]
    )
    return candidates


def load_config(explicit: Optional[Path] = None) -> tuple[dict[str, Any], str, Optional[Path]]:
    seen: set[Path] = set()
    for source, raw_path in default_config_paths(explicit):
        path = raw_path.expanduser()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            if source in {"explicit-config", "environment-config"}:
                return {"_config_error": "missing"}, source, path.parent
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"_config_error": str(exc)}, source, path.parent
        if isinstance(payload, dict):
            return payload, source, path.parent
        return {"_config_error": "not-an-object"}, source, path.parent
    return {}, "none", None


def _looks_like_path(value: str) -> bool:
    return (
        value.startswith((".", "~"))
        or "/" in value
        or "\\" in value
        or Path(value).suffix.lower() in {".ps1", ".py"}
    )


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def expand_command(value: Any, base_dir: Optional[Path] = None) -> tuple[list[str], Optional[Path]]:
    if isinstance(value, list):
        if not value or any(not isinstance(item, str) or not item.strip() for item in value):
            return [], None
        parts = [os.path.expandvars(os.path.expanduser(item.strip())) for item in value]
    elif isinstance(value, str) and value.strip():
        expanded = os.path.expandvars(os.path.expanduser(value.strip()))
        unquoted = _strip_outer_quotes(expanded)
        direct_path = Path(unquoted)
        if base_dir and not direct_path.is_absolute() and _looks_like_path(unquoted):
            direct_path = base_dir / direct_path
        if direct_path.exists() or (_looks_like_path(unquoted) and not any(char.isspace() for char in unquoted)):
            parts = [str(direct_path)]
        else:
            try:
                parts = [_strip_outer_quotes(item) for item in shlex.split(expanded, posix=os.name != "nt")]
            except ValueError:
                return [], None
    else:
        return [], None

    if not parts:
        return [], None

    candidate = Path(parts[0]).expanduser()
    if base_dir and not candidate.is_absolute() and _looks_like_path(parts[0]):
        candidate = base_dir / candidate
        parts[0] = str(candidate)
    if candidate.suffix.lower() == ".ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return [], candidate
        return [shell, "-NoProfile", "-File", str(candidate), *parts[1:]], candidate
    if candidate.suffix.lower() == ".py":
        return [sys.executable, str(candidate), *parts[1:]], candidate

    resolved = shutil.which(parts[0])
    origin = Path(resolved) if resolved else (candidate if candidate.exists() else None)
    return parts, origin


def command_exists(command: list[str], origin: Optional[Path]) -> bool:
    if not command:
        return False
    if origin is not None:
        if not origin.is_file():
            return False
        if origin.suffix.lower() not in {".py", ".ps1"} and command[0] == str(origin):
            return os.name == "nt" or os.access(origin, os.X_OK)
        return True
    return bool(shutil.which(command[0]))


def credential_file_paths() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    if os.environ.get("FEISHU_ENV_FILE"):
        candidates.append(("explicit-env-file", Path(os.environ["FEISHU_ENV_FILE"])))

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    candidates.extend(
        [
            ("codex-secret-file", codex_home / "secrets" / "research-sheet-pipeline" / "feishu.env"),
            ("user-secret-file", Path.home() / ".config" / "research-sheet-pipeline" / "feishu.env"),
        ]
    )
    return candidates


def credential_state() -> dict[str, str]:
    has_app_id = bool(os.environ.get("FEISHU_APP_ID", "").strip())
    has_app_secret = bool(os.environ.get("FEISHU_APP_SECRET", "").strip())
    app_id_source = "environment" if has_app_id else None
    app_secret_source = "environment" if has_app_secret else None
    if has_app_id and has_app_secret:
        return {"status": "present", "source": "environment"}

    seen: set[Path] = set()
    for source, raw_path in credential_file_paths():
        path = raw_path.expanduser()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        try:
            values = parse_env_text(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not has_app_id and bool(values.get("FEISHU_APP_ID", "").strip()):
            has_app_id = True
            app_id_source = source
        if not has_app_secret and bool(values.get("FEISHU_APP_SECRET", "").strip()):
            has_app_secret = True
            app_secret_source = source
        if has_app_id and has_app_secret:
            combined_source = app_id_source if app_id_source == app_secret_source else "combined"
            return {"status": "present", "source": combined_source or "combined"}
    if has_app_id or has_app_secret:
        partial_source = app_id_source or app_secret_source or "unknown"
        return {"status": "partial", "source": partial_source}
    return {"status": "missing", "source": "none"}


def resolve_cli(
    explicit_command: Any,
    config: dict[str, Any],
    config_source: str = "none",
    config_dir: Optional[Path] = None,
    pin: bool = False,
    required_capabilities: Optional[list[str]] = None,
) -> dict[str, Any]:
    document_config = config.get("document") if isinstance(config.get("document"), dict) else {}
    required = list(dict.fromkeys(required_capabilities or []))
    candidates: list[tuple[str, Any, Optional[Path]]] = []
    explicit_config = config_source in {"explicit-config", "environment-config"}
    if explicit_command:
        candidates.append(("explicit-command", explicit_command, None))
    elif explicit_config and document_config.get("command"):
        candidates.append(("explicit-config", document_config["command"], config_dir))

    if not pin:
        if explicit_command and explicit_config and document_config.get("command"):
            candidates.append(("explicit-config", document_config["command"], config_dir))
        if os.environ.get(CLI_ENV):
            candidates.append(("environment-command", os.environ[CLI_ENV], None))
        if document_config.get("command") and not explicit_config:
            candidates.append(("local-config", document_config["command"], config_dir))

        for executable in ("feishu-doc", "feishu-doc-cli", "feishu-doc.ps1"):
            found = shutil.which(executable)
            if found:
                candidates.append(("path", found, None))
                break

        bundled = Path(__file__).with_name("feishu_doc.py")
        candidates.append(("bundled", str(bundled), None))

    for source, value, base_dir in candidates:
        command, origin = expand_command(value, base_dir=base_dir)
        if not command_exists(command, origin):
            continue
        is_bundled = source == "bundled"
        if is_bundled:
            capabilities = BUNDLED_CAPABILITIES
            missing_capabilities = [item for item in required if item not in capabilities]
            credentials = credential_state()
            if missing_capabilities:
                status = "capability_mismatch"
            elif credentials["status"] == "present":
                status = "ready"
            else:
                status = "needs_credentials"
        else:
            capabilities = []
            missing_capabilities = required
            credentials = {"status": "external_unverified", "source": "provider"}
            status = "legacy_unverified"
        return {
            "status": status,
            "provider": "feishu-cli",
            "source": source,
            "provider_ref": f"{source}:feishu-cli",
            "credentials": credentials,
            "capabilities": capabilities,
            "required_capabilities": required,
            "missing_capabilities": missing_capabilities,
            "capability_check": "complete" if is_bundled else "required",
            "user_action": (
                None
                if status == "ready"
                else "Run one read-only legacy CLI preflight before mutation."
                if status == "legacy_unverified"
                else "Select a provider that supports every required document capability."
                if status == "capability_mismatch"
                else (
                    "Configure FEISHU_APP_ID and FEISHU_APP_SECRET locally via environment, "
                    "FEISHU_ENV_FILE, or the documented per-user secret file. Never paste the secret into chat."
                )
            ),
            "_command": command,
            "_origin": str(origin) if origin else None,
        }

    return {
        "status": "unavailable",
        "provider": "feishu-cli",
        "source": "none",
        "provider_ref": None,
        "credentials": {"status": "unknown", "source": "none"},
        "capabilities": [],
        "required_capabilities": required,
        "missing_capabilities": required,
        "user_action": "Install a compatible document provider or select an authenticated Agent connector.",
        "_command": [],
        "_origin": None,
    }


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if not key.startswith("_")}


def redact_provider_output(value: str, result: dict[str, Any]) -> str:
    output = value
    private_values = [os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET"), result.get("_origin")]
    for item in result.get("_command", []):
        if isinstance(item, str) and ("/" in item or "\\" in item or re_drive_path(item)):
            private_values.append(item)
    private_strings = {str(item) for item in private_values if item}
    private_strings.update(item.replace("\\", "/") for item in list(private_strings) if "\\" in item)
    for private in sorted(private_strings, key=len, reverse=True):
        output = output.replace(private, "<redacted>")
    return output


def re_drive_path(value: str) -> bool:
    return len(value) >= 2 and value[0].isalpha() and value[1] == ":"


def run_provider(result: dict[str, Any], provider_args: list[str], timeout: int) -> int:
    if result.get("status") not in {"ready", "legacy_unverified"}:
        print(json.dumps(public_result(result), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if provider_args and provider_args[0] == "--":
        provider_args = provider_args[1:]
    if not provider_args:
        print("A provider command is required after --run --.", file=sys.stderr)
        return 2
    try:
        completed = subprocess.run(
            [*result["_command"], *provider_args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        print("Document provider timed out.", file=sys.stderr)
        return 124
    except OSError:
        print("Document provider could not be started.", file=sys.stderr)
        return 2
    if completed.stdout:
        stdout = redact_provider_output(completed.stdout, result)
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if completed.stderr:
        stderr = redact_provider_output(completed.stderr, result)
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    return completed.returncode


def resolve(args: argparse.Namespace) -> dict[str, Any]:
    config, config_source, config_dir = load_config(args.config)
    document_config = config.get("document") if isinstance(config.get("document"), dict) else {}
    explicit_config = config_source in {"explicit-config", "environment-config"}
    if args.provider:
        provider = args.provider
    elif explicit_config and document_config.get("provider"):
        provider = document_config["provider"]
    else:
        provider = os.environ.get(PROVIDER_ENV) or document_config.get("provider") or "auto"
    if args.connector:
        connector = args.connector
    elif args.provider:
        connector = None
    else:
        connector = document_config.get("connector")
    required_capabilities = list(
        dict.fromkeys(item.strip() for item in (args.required_capability or []) if item.strip())
    )

    if config.get("_config_error"):
        return {
            "status": "invalid_config",
            "provider": provider,
            "config_source": config_source,
            "error": "The explicit local provider config is missing or invalid.",
        }
    configured_command = document_config.get("command")
    if configured_command is not None and not (
        isinstance(configured_command, str) and bool(configured_command.strip())
        or isinstance(configured_command, list)
        and bool(configured_command)
        and all(isinstance(item, str) and bool(item.strip()) for item in configured_command)
    ):
        return {
            "status": "invalid_config",
            "provider": provider if isinstance(provider, str) else "invalid",
            "config_source": config_source,
            "error": "The configured document command must be a non-empty string or string array.",
        }
    if not isinstance(provider, str) or not provider.strip():
        return {
            "status": "invalid_config",
            "provider": "invalid",
            "config_source": config_source,
            "error": "The configured document provider must be a non-empty string.",
        }
    provider = provider.strip().lower()
    if connector is not None and (not isinstance(connector, str) or not connector.strip()):
        return {
            "status": "invalid_config",
            "provider": provider,
            "config_source": config_source,
            "error": "The configured Agent connector must be a non-empty string.",
        }
    if isinstance(connector, str):
        connector = connector.strip()
    if provider == "none":
        return {"status": "disabled", "provider": "none", "config_source": config_source}
    if provider == "connector" or connector:
        return {
            "status": "connector_check_required",
            "provider": "connector",
            "connector": connector or "auto",
            "config_source": config_source,
            "required_capabilities": required_capabilities,
            "capability_check": "required",
            "user_action": "The Agent must verify the connected provider exposes every required capability before mutation.",
        }
    if provider not in {"auto", "feishu", "feishu-cli"}:
        return {
            "status": "unsupported_provider",
            "provider": provider,
            "config_source": config_source,
        }

    result = resolve_cli(
        args.command,
        config,
        config_source=config_source,
        config_dir=config_dir,
        pin=args.pin,
        required_capabilities=required_capabilities,
    )
    result["config_source"] = config_source
    result["connector_fallback_allowed"] = provider == "auto"
    return result


def resolution_exit_code(result: dict[str, Any]) -> int:
    status = result.get("status")
    if status in {"ready", "disabled"}:
        return 0
    if status == "needs_credentials":
        return 4
    if status in {"unavailable", "capability_mismatch", "connector_check_required", "legacy_unverified"}:
        return 3
    return 2


def self_test() -> None:
    global credential_file_paths, credential_state

    assert parse_env_text("A=1\n#x\nB='two'") == {"A": "1", "B": "two"}
    command, origin = expand_command(str(Path(__file__).with_name("feishu_doc.py")))
    assert command and origin and origin.name == "feishu_doc.py"
    private = {"status": "ready", "_command": ["super-secret-sentinel"]}
    assert "super-secret-sentinel" not in json.dumps(public_result(private))
    redacted = redact_provider_output(
        r"started C:\private\feishu-doc.ps1 with app-secret-value",
        {"_command": [r"C:\private\feishu-doc.ps1"], "_origin": r"C:\private\feishu-doc.ps1"},
    )
    assert "C:\\private" not in redacted

    original_which = shutil.which
    original_credential_paths = credential_file_paths
    original_credential_state = credential_state
    saved_credentials = {
        key: os.environ.pop(key, None)
        for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ENV_FILE", CLI_ENV)
    }
    try:
        shutil.which = lambda name: r"C:\safe-runtime\pwsh.exe" if name == "pwsh" else None
        quoted = r'"C:\path with space\feishu-doc.ps1" --flag'
        quoted_command, quoted_origin = expand_command(quoted)
        assert quoted_origin == Path(r"C:\path with space\feishu-doc.ps1")
        assert quoted_command[-1] == "--flag" and '"' not in quoted_command[-2]
        assert "-ExecutionPolicy" not in quoted_command
        assert not command_exists(quoted_command, quoted_origin)

        shutil.which = lambda _name: None
        for missing in ("definitely-missing-provider.ps1", "definitely-missing-provider.py"):
            missing_command, missing_origin = expand_command(missing)
            assert not command_exists(missing_command, missing_origin)

        external = resolve_cli(
            str(Path(__file__)),
            {},
            pin=True,
            required_capabilities=["document.read"],
        )
        assert external["status"] == "legacy_unverified" and external["capabilities"] == []
        assert external["missing_capabilities"] == ["document.read"]

        relative = resolve_cli(
            None,
            {"document": {"command": "feishu_doc.py"}},
            config_source="explicit-config",
            config_dir=Path(__file__).parent,
            pin=True,
        )
        assert relative["status"] == "legacy_unverified"
        assert Path(relative["_origin"]) == Path(__file__).with_name("feishu_doc.py")

        os.environ[CLI_ENV] = str(Path(__file__))
        preferred = resolve_cli(
            None,
            {"document": {"command": "feishu_doc.py"}},
            config_source="explicit-config",
            config_dir=Path(__file__).parent,
        )
        assert preferred["source"] == "explicit-config"
        os.environ.pop(CLI_ENV, None)

        class FakeCredentialPath:
            def __init__(self, text: str) -> None:
                self.text = text

            def expanduser(self) -> "FakeCredentialPath":
                return self

            def is_file(self) -> bool:
                return True

            def read_text(self, encoding: str = "utf-8") -> str:
                return self.text

        partial = FakeCredentialPath("FEISHU_APP_ID=test-id")
        complete = FakeCredentialPath("FEISHU_APP_ID=test-id\nFEISHU_APP_SECRET=test-secret")
        credential_file_paths = lambda: [("partial-first", partial), ("complete-second", complete)]  # type: ignore[assignment]
        assert credential_state() == {"status": "present", "source": "combined"}

        credential_state = lambda: {"status": "present", "source": "test"}  # type: ignore[assignment]
        bundled = resolve_cli(
            None,
            {},
            required_capabilities=["permission.public.read", "permission.public.anyone_editable"],
        )
        assert bundled["status"] == "ready"
        assert bundled["capabilities"] == BUNDLED_CAPABILITIES
        assert resolution_exit_code(bundled) == 0
        assert resolution_exit_code({"status": "needs_credentials"}) == 4
        assert resolution_exit_code({"status": "legacy_unverified"}) != 0
        assert resolution_exit_code({"status": "unavailable"}) != 0
    finally:
        shutil.which = original_which
        credential_file_paths = original_credential_paths
        credential_state = original_credential_state
        for key, value in saved_credentials.items():
            if value is not None:
                os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve the document provider for research-sheet-pipeline.")
    parser.add_argument("--provider", choices=("auto", "feishu", "feishu-cli", "connector", "none"))
    parser.add_argument("--command", help="Explicit CLI path or command. Prefer a local config or environment variable.")
    parser.add_argument("--pin", action="store_true", help="Fail closed if the explicit command is unavailable.")
    parser.add_argument("--connector", help="Agent connector/provider name selected outside this script.")
    parser.add_argument(
        "--required-capability",
        action="append",
        default=[],
        help="Capability required by the current document job; repeat for multiple capabilities.",
    )
    parser.add_argument("--config", type=Path, help="Explicit local provider config JSON.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--run", action="store_true", help="Run the resolved CLI without exposing its locator.")
    parser.add_argument("--self-test", action="store_true")
    args, provider_args = parser.parse_known_args()

    if provider_args and not args.run:
        parser.error(f"unrecognized arguments: {' '.join(provider_args)}")

    if args.self_test:
        self_test()
        print("self-test passed")
        return 0

    result = resolve(args)
    if args.run:
        return run_provider(result, provider_args, timeout=max(1, min(args.timeout, 300)))
    result = public_result(result)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result.get('status')}: {result.get('provider')}")
        if result.get("user_action"):
            print(result["user_action"])
    return resolution_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
