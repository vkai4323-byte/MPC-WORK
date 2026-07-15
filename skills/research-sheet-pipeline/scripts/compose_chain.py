#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


VALID_SHEET_MODES = {"none", "existing", "create"}
VALID_DOC_MODES = {"none", "create", "update"}
VALID_WRITE_MODES = {"none", "append", "fill", "upsert"}
FEISHU_SYSTEMS = {"auto", "feishu", "feishu-cli"}
VALID_PROVIDER_MODES = {"auto", "pin", "prefer", "connector", "none"}
VALID_DOCUMENT_PERMISSIONS = {"", "none", "anyone_editable"}
DEFAULT_LIMITS = {
    "preflight_seconds": 60,
    "popo_read_seconds": 90,
    "api_batch_seconds": 120,
    "document_transaction_seconds": 120,
    "fallbacks_per_stage": 1,
}
MAX_LIMITS = {
    "preflight_seconds": 300,
    "popo_read_seconds": 300,
    "api_batch_seconds": 600,
    "document_transaction_seconds": 600,
    "fallbacks_per_stage": 2,
}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def document_capabilities(documents: dict) -> list[str]:
    mode = documents.get("mode", "none")
    if mode == "none":
        return []
    capabilities = ["document.read", "document.update", "document.readback", "document.structure.read"]
    if mode == "create":
        capabilities.insert(1, "document.copy")
    permission = str(documents.get("permission") or "").strip().lower()
    if permission == "anyone_editable":
        capabilities.extend(["permission.public.read", "permission.public.anyone_editable"])
    return capabilities


def object_section(job: dict, name: str, errors: list[str]) -> dict:
    value = job.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    return value


def validate_document_provider(tools: dict, documents: dict, errors: list[str]) -> None:
    system = documents.get("system")
    if system is not None:
        if not isinstance(system, str) or system.strip().lower() not in FEISHU_SYSTEMS:
            errors.append("documents.system must be one of: auto, feishu, feishu-cli")

    permission = documents.get("permission")
    if permission is None:
        permission = ""
    if not isinstance(permission, str) or permission.strip().lower() not in VALID_DOCUMENT_PERMISSIONS:
        errors.append("documents.permission must be empty, none, or anyone_editable")

    configured = tools.get("document_provider")
    if configured is not None:
        if isinstance(configured, str):
            if configured.strip().lower() not in VALID_PROVIDER_MODES:
                errors.append("tools.document_provider mode must be one of: auto, pin, prefer, connector, none")
        elif isinstance(configured, dict):
            platform = configured.get("platform", "feishu")
            mode = configured.get("mode", "auto")
            if not isinstance(platform, str) or platform.strip().lower() not in FEISHU_SYSTEMS:
                errors.append("tools.document_provider.platform must be one of: auto, feishu, feishu-cli")
            if not isinstance(mode, str) or mode.strip().lower() not in VALID_PROVIDER_MODES:
                errors.append("tools.document_provider.mode must be one of: auto, pin, prefer, connector, none")
        else:
            errors.append("tools.document_provider must be an object or mode string")

    if "feishu_cli" in tools:
        legacy = tools["feishu_cli"]
        valid_legacy = (
            isinstance(legacy, str) and bool(legacy.strip())
        ) or (
            isinstance(legacy, list)
            and bool(legacy)
            and all(isinstance(item, str) and bool(item.strip()) for item in legacy)
        )
        if not valid_legacy:
            errors.append("tools.feishu_cli must be a non-empty command string or string array")


def document_resolution(tools: dict, documents: dict) -> tuple[dict, list[dict]]:
    diagnostics: list[dict] = []
    configured = tools.get("document_provider", {"platform": "feishu", "mode": "auto"})
    legacy_cli = tools.get("feishu_cli")

    if isinstance(configured, str):
        provider = {"platform": "feishu", "mode": configured}
    elif isinstance(configured, dict):
        provider = configured
    else:
        provider = {"platform": "feishu", "mode": "auto"}
        diagnostics.append({
            "severity": "warning",
            "code": "DP_INVALID_CONFIG_IGNORED",
            "message": "tools.document_provider must be an object or mode string; auto mode will be used.",
        })

    platform = str(provider.get("platform") or documents.get("system") or "feishu").strip().lower()
    mode = str(provider.get("mode") or "auto").strip().lower()
    source_hint = "manifest"
    if legacy_cli and "document_provider" not in tools:
        platform = "feishu"
        mode = "pin"
        source_hint = "manifest_legacy"
        diagnostics.append({
            "severity": "warning",
            "code": "LEGACY_FEISHU_CLI",
            "message": "tools.feishu_cli is deprecated; move the command to per-user provider config.",
        })
    elif legacy_cli:
        diagnostics.append({
            "severity": "warning",
            "code": "LEGACY_FEISHU_CLI_IGNORED",
            "message": "tools.feishu_cli was ignored because tools.document_provider is present.",
        })

    if platform in FEISHU_SYSTEMS:
        platform = "feishu"
    return {
        "required": True,
        "platform": platform,
        "mode": mode,
        "status": "preflight_required",
        "required_capabilities": document_capabilities(documents),
        "selected": None,
        "source_hint": source_hint,
    }, diagnostics


def invalid_result(errors: list[str]) -> dict:
    return {
        "schema_version": "2.0",
        "valid": False,
        "runnable": False,
        "errors": errors,
        "chain": [],
        "tool_plan": [],
        "provider_resolution": {},
        "diagnostics": [],
    }


def resolve_execution(execution: dict, errors: list[str]) -> tuple[dict, str]:
    limits = dict(DEFAULT_LIMITS)
    for key, default in DEFAULT_LIMITS.items():
        value = execution.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_LIMITS[key]:
            errors.append(f"execution.{key} must be an integer between 1 and {MAX_LIMITS[key]}")
        else:
            limits[key] = value
    artifact_value = execution.get("artifact_dir", ".codex-runs/research-sheet-pipeline/run")
    if not isinstance(artifact_value, str) or not artifact_value.strip():
        errors.append("execution.artifact_dir must be a non-empty string")
        artifact_dir = ".codex-runs/research-sheet-pipeline/run"
    else:
        artifact_dir = artifact_value.strip()
    normalized_artifact_dir = artifact_dir.replace("\\", "/")
    if (
        normalized_artifact_dir.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:", normalized_artifact_dir)
        or any(ord(char) < 32 for char in normalized_artifact_dir)
    ):
        errors.append("execution.artifact_dir must be relative")
    elif ".." in normalized_artifact_dir.split("/"):
        errors.append("execution.artifact_dir must stay within the workspace")
    return limits, artifact_dir


def compose(job: dict) -> dict:
    if not isinstance(job, dict):
        return invalid_result(["job manifest must be an object"])

    errors: list[str] = []
    objective = job.get("objective")
    sheet = object_section(job, "sheet", errors)
    research = object_section(job, "research", errors)
    documents = object_section(job, "documents", errors)
    writeback = object_section(job, "writeback", errors)
    verification = object_section(job, "verification", errors)
    tools = object_section(job, "tools", errors)
    execution = object_section(job, "execution", errors)

    sheet_mode = sheet.get("mode", "none")
    doc_mode = documents.get("mode", "none")
    write_mode = writeback.get("mode", "none")
    research_enabled = bool(research.get("enabled", False))

    require(isinstance(objective, str) and bool(objective.strip()), "objective is required", errors)
    if "job_id" in job:
        require(isinstance(job["job_id"], str), "job_id must be a string", errors)
    require(isinstance(sheet_mode, str) and sheet_mode in VALID_SHEET_MODES, f"invalid sheet.mode: {sheet_mode}", errors)
    require(isinstance(doc_mode, str) and doc_mode in VALID_DOC_MODES, f"invalid documents.mode: {doc_mode}", errors)
    require(isinstance(write_mode, str) and write_mode in VALID_WRITE_MODES, f"invalid writeback.mode: {write_mode}", errors)
    if "platform" in sheet:
        require(isinstance(sheet["platform"], str), "sheet.platform must be a string", errors)

    if sheet_mode == "existing":
        require(isinstance(sheet.get("url"), str) and bool(sheet["url"].strip()), "sheet.url is required for an existing sheet", errors)
    if sheet_mode == "create":
        require(bool(sheet.get("platform")), "sheet.platform is required when creating a sheet", errors)
    if write_mode != "none":
        require(sheet_mode != "none", "writeback requires an existing or new sheet", errors)
        require(isinstance(sheet.get("key"), str) and bool(sheet["key"].strip()), "sheet.key is required for writeback", errors)
    if "enabled" in research:
        require(isinstance(research["enabled"], bool), "research.enabled must be a boolean", errors)
    if research_enabled:
        fields = research.get("fields")
        require(
            isinstance(fields, list)
            and bool(fields)
            and all(isinstance(item, str) and bool(item.strip()) for item in fields),
            "research.fields must be a non-empty string array when research is enabled",
            errors,
        )
    if "sources" in research:
        sources = research["sources"]
        require(
            isinstance(sources, list) and all(isinstance(item, str) and bool(item.strip()) for item in sources),
            "research.sources must be a string array",
            errors,
        )
    if doc_mode != "none":
        require(bool(documents.get("system")), "documents.system is required", errors)
        if doc_mode == "create":
            require(
                isinstance(documents.get("template"), str) and bool(documents["template"].strip()),
                "documents.template is required for document creation",
                errors,
            )
        validate_document_provider(tools, documents, errors)

    for key in ("readback", "visual"):
        if key in verification:
            require(isinstance(verification[key], bool), f"verification.{key} must be a boolean", errors)

    for key in ("browser_skill", "sheet_skill", "bilibili_script"):
        if key in tools:
            require(isinstance(tools[key], str) and bool(tools[key].strip()), f"tools.{key} must be a non-empty string", errors)

    execution_limits, artifact_dir = resolve_execution(execution, errors)

    if errors:
        return invalid_result(errors)

    chain = ["scope"]
    checkpoints = ["Confirm objective, fields, entity boundary, and completion criteria."]

    if sheet_mode == "existing":
        chain.append("sheet_context")
        checkpoints.append("Read live headers, key values, target cells, and formats.")
    elif sheet_mode == "create":
        chain.append("sheet_schema")
        checkpoints.append("Approve a unique key, columns, types, and blank policy.")

    if research_enabled:
        chain.append("web_research")
        checkpoints.append("Capture requested fields with source URL/ID and observation time; add screenshots only for UI-only or ambiguous evidence.")

    needs_records = research_enabled or doc_mode != "none" or write_mode != "none" or sheet_mode == "create"
    if needs_records:
        chain.append("normalize")
        checkpoints.append("Stop on duplicate keys, ambiguous matches, or unresolved required fields.")

    if doc_mode != "none":
        chain.append("document")
        checkpoints.append("Resolve a ready provider, then use one dry-run/apply/read-back transaction and verify structure plus sharing state.")

    if sheet_mode == "create":
        chain.append("sheet_create")
        checkpoints.append("Verify the empty schema and baseline formatting before loading data.")
    if write_mode != "none":
        chain.append("sheet_writeback")
        checkpoints.append("Re-read before writing and join by the live sheet key, never row number.")

    has_side_effect = doc_mode != "none" or sheet_mode == "create" or write_mode != "none"
    if has_side_effect or verification.get("readback") or verification.get("visual"):
        chain.append("verify")
        checkpoints.append("Read back exact values and reconcile inserted, updated, skipped, and failed counts.")

    tool_plan = []
    if sheet.get("platform", "").lower() == "popo" and sheet_mode != "none":
        popo_modules = [item for item in ("sheet_context", "sheet_schema", "sheet_create", "sheet_writeback") if item in chain]
        if sheet_mode == "create" or write_mode != "none":
            popo_modules.extend(item for item in ("verify",) if item in chain)
        tool_plan.append({
            "modules": popo_modules,
            "tools": [tools.get("browser_skill", "kimi-webbridge"), tools.get("sheet_skill", "popo-sheet")],
            "note": "Reuse one authenticated task tab. Apply the 90-second read-only gate or bounded writeback gate.",
        })
    if research_enabled:
        sources = " ".join(str(value).lower() for value in (research.get("sources") or []))
        is_bilibili = any(token in sources for token in ("bilibili", "b站", "哔哩"))
        research_tools = [tools.get("bilibili_script", "scripts/bilibili_batch.py"), tools.get("browser_skill", "kimi-webbridge")] if is_bilibili else [tools.get("browser_skill", "kimi-webbridge")]
        tool_plan.append({
            "modules": ["web_research"],
            "tools": research_tools,
            "note": "Use the API/batch route first when listed; send only unresolved or login-dependent records to the browser.",
        })

    provider_resolution = {}
    diagnostics: list[dict] = []
    if doc_mode != "none":
        resolution, provider_diagnostics = document_resolution(tools, documents)
        provider_resolution["document"] = resolution
        diagnostics.extend(provider_diagnostics)
        tool_plan.append({
            "modules": ["document"],
            "tools": [f"document-provider:{resolution['platform']}"],
            "provider_ref": None,
            "note": "Run the portable provider preflight; never serialize commands, paths, or credentials into the job plan.",
        })

    return {
        "schema_version": "2.0",
        "valid": True,
        "runnable": doc_mode == "none",
        "job_id": job.get("job_id", ""),
        "chain": chain,
        "checkpoints": checkpoints,
        "tool_plan": tool_plan,
        "provider_resolution": provider_resolution,
        "diagnostics": diagnostics,
        "artifact_dir": artifact_dir,
        "execution_limits": execution_limits,
    }


def self_test() -> None:
    assert not compose([])["valid"]  # type: ignore[arg-type]
    malformed = compose({"objective": "test", "sheet": ["not-an-object"]})
    assert not malformed["valid"] and "sheet must be an object" in malformed["errors"]

    base = {
        "objective": "test",
        "sheet": {"mode": "none"},
        "research": {"enabled": False},
        "writeback": {"mode": "none"},
        "verification": {},
        "tools": {"document_provider": {"platform": "feishu", "mode": "auto"}},
        "documents": {
            "mode": "update",
            "system": "feishu",
            "permission": "anyone_editable",
        },
    }
    result = compose(base)
    required = result["provider_resolution"]["document"]["required_capabilities"]
    assert result["valid"] and not result["runnable"]
    assert "permission.public.read" in required
    assert "permission.public.anyone_editable" in required
    assert "permission.write" not in required

    invalid_permission = json.loads(json.dumps(base))
    invalid_permission["documents"]["permission"] = "organization_editable"
    assert not compose(invalid_permission)["valid"]

    invalid_provider = json.loads(json.dumps(base))
    invalid_provider["tools"]["document_provider"]["mode"] = "surprise"
    assert not compose(invalid_provider)["valid"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a research-sheet job and compose its module chain.")
    parser.add_argument("job", type=Path, nargs="?")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("self-test passed")
        return 0
    if args.job is None:
        parser.error("job is required unless --self-test is used")

    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read job manifest: {exc}", file=sys.stderr)
        return 2

    result = compose(job)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["valid"]:
        print(" -> ".join(result["chain"]))
        print("Tools:")
        for item in result["tool_plan"]:
            print(f"- {', '.join(item['modules'])}: {' + '.join(item['tools'])}")
        for index, item in enumerate(result["checkpoints"], 1):
            print(f"{index}. {item}")
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
