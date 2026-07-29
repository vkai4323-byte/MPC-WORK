#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


SCHEMA_VERSION = "3.0"
VALID_SHEET_MODES = {"none", "existing", "create"}
VALID_DOC_MODES = {"none", "create", "update"}
VALID_WRITE_MODES = {"none", "append", "fill", "upsert"}
VALID_WRITE_INTENTS = {
    "audit_only",
    "fill_missing",
    "refresh_existing",
    "merge_existing",
    "append",
    "upsert",
}
VALID_SCOPE_SELECTIONS = {"all_eligible", "blank_only", "named"}
VALID_EXISTING_VALUE_POLICIES = {"preserve", "overwrite", "merge"}
VALID_SOURCE_KEY_POLICIES = {"exact_structured"}
VALID_ARTIFACT_MODES = {"minimal", "debug_on_failure", "debug"}
VALID_RESUME_MODES = {"none", "checkpoint"}
VALID_DESTINATION_TYPES = {"sheet", "document"}
VALID_STAGE_STATES = {"pending", "running", "ready", "verified", "blocked", "unknown"}
VALID_ELIGIBILITY_STATUSES = {"eligible", "skipped", "blocked"}
VALID_CHECKPOINT_STATUSES = {"new", "resuming"}
VALID_MODULES = {
    "scope",
    "sheet_context",
    "sheet_schema",
    "web_research",
    "normalize",
    "document",
    "sheet_create",
    "sheet_writeback",
    "verify",
}
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
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def valid_readback_proof(value: object) -> bool:
    return (
        isinstance(value, dict)
        and valid_sha256(value.get("readback_hash"))
        and non_empty_string(value.get("observed_at"))
    )


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def contract_hash(job: dict) -> str:
    return canonical_hash(
        {key: value for key, value in job.items() if key != "state"}
    )


def checkpoint_hash(job: dict) -> str:
    payload = json.loads(json.dumps(job, ensure_ascii=False))
    state = payload.get("state")
    if isinstance(state, dict):
        state["checkpoint_hash"] = ""
    return canonical_hash(payload)


def object_section(job: dict, name: str, errors: list[str]) -> dict:
    value = job.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    return value


def document_capabilities(documents: dict) -> list[str]:
    mode = documents.get("mode", "none")
    if mode == "none":
        return []
    capabilities = [
        "document.read",
        "document.update",
        "document.readback",
        "document.structure.read",
    ]
    if mode == "create":
        capabilities.insert(1, "document.copy")
    permission = str(documents.get("permission") or "").strip().lower()
    if permission == "anyone_editable":
        capabilities.extend(
            ["permission.public.read", "permission.public.anyone_editable"]
        )
    return capabilities


def validate_document_provider(
    tools: dict, documents: dict, errors: list[str]
) -> None:
    system = documents.get("system")
    if system is not None:
        if not isinstance(system, str) or system.strip().lower() not in FEISHU_SYSTEMS:
            errors.append(
                "documents.system must be one of: auto, feishu, feishu-cli"
            )

    permission = documents.get("permission")
    if permission is None:
        permission = ""
    if (
        not isinstance(permission, str)
        or permission.strip().lower() not in VALID_DOCUMENT_PERMISSIONS
    ):
        errors.append("documents.permission must be empty, none, or anyone_editable")

    configured = tools.get("document_provider")
    if configured is not None:
        if isinstance(configured, str):
            if configured.strip().lower() not in VALID_PROVIDER_MODES:
                errors.append(
                    "tools.document_provider mode must be one of: "
                    "auto, pin, prefer, connector, none"
                )
        elif isinstance(configured, dict):
            platform = configured.get("platform", "feishu")
            mode = configured.get("mode", "auto")
            if (
                not isinstance(platform, str)
                or platform.strip().lower() not in FEISHU_SYSTEMS
            ):
                errors.append(
                    "tools.document_provider.platform must be one of: "
                    "auto, feishu, feishu-cli"
                )
            if (
                not isinstance(mode, str)
                or mode.strip().lower() not in VALID_PROVIDER_MODES
            ):
                errors.append(
                    "tools.document_provider.mode must be one of: "
                    "auto, pin, prefer, connector, none"
                )
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
            errors.append(
                "tools.feishu_cli must be a non-empty command string or string array"
            )


def document_resolution(tools: dict, documents: dict) -> tuple[dict, list[dict]]:
    diagnostics: list[dict] = []
    configured = tools.get(
        "document_provider", {"platform": "feishu", "mode": "auto"}
    )
    legacy_cli = tools.get("feishu_cli")

    if isinstance(configured, str):
        provider = {"platform": "feishu", "mode": configured}
    elif isinstance(configured, dict):
        provider = configured
    else:
        provider = {"platform": "feishu", "mode": "auto"}
        diagnostics.append(
            {
                "severity": "warning",
                "code": "DP_INVALID_CONFIG_IGNORED",
                "message": (
                    "tools.document_provider must be an object or mode string; "
                    "auto mode will be used."
                ),
            }
        )

    platform = str(
        provider.get("platform") or documents.get("system") or "feishu"
    ).strip().lower()
    mode = str(provider.get("mode") or "auto").strip().lower()
    source_hint = "manifest"
    if legacy_cli and "document_provider" not in tools:
        platform = "feishu"
        mode = "pin"
        source_hint = "manifest_legacy"
        diagnostics.append(
            {
                "severity": "warning",
                "code": "LEGACY_FEISHU_CLI",
                "message": (
                    "tools.feishu_cli is deprecated; move the command to "
                    "per-user provider config."
                ),
            }
        )
    elif legacy_cli:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "LEGACY_FEISHU_CLI_IGNORED",
                "message": (
                    "tools.feishu_cli was ignored because "
                    "tools.document_provider is present."
                ),
            }
        )

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
        "schema_version": SCHEMA_VERSION,
        "valid": False,
        "runnable": False,
        "errors": errors,
        "chain": [],
        "checkpoints": [],
        "tool_plan": [],
        "provider_resolution": {},
        "run_contract": {},
        "resume_contract": {},
        "artifact_policy": {},
        "diagnostics": [],
    }


def relative_path(
    value: object, field: str, default: str, errors: list[str]
) -> str:
    if not non_empty_string(value):
        errors.append(f"{field} must be a non-empty string")
        return default
    path = str(value).strip()
    normalized = path.replace("\\", "/")
    if (
        normalized.startswith(("/", "~"))
        or re.match(r"^[A-Za-z]:", normalized)
        or any(ord(char) < 32 for char in normalized)
    ):
        errors.append(f"{field} must be relative")
    elif ".." in normalized.split("/"):
        errors.append(f"{field} must stay within the workspace")
    return path


def resolve_execution(execution: dict, errors: list[str]) -> dict:
    limits = dict(DEFAULT_LIMITS)
    for key, default in DEFAULT_LIMITS.items():
        value = execution.get(key, default)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= MAX_LIMITS[key]
        ):
            errors.append(
                f"execution.{key} must be an integer between 1 and {MAX_LIMITS[key]}"
            )
        else:
            limits[key] = value

    artifact_dir = relative_path(
        execution.get(
            "artifact_dir", ".codex-runs/research-sheet-pipeline/run"
        ),
        "execution.artifact_dir",
        ".codex-runs/research-sheet-pipeline/run",
        errors,
    )
    artifact_mode = execution.get("artifact_mode", "minimal")
    if artifact_mode not in VALID_ARTIFACT_MODES:
        errors.append(
            "execution.artifact_mode must be one of: "
            + ", ".join(sorted(VALID_ARTIFACT_MODES))
        )
        artifact_mode = "minimal"

    resume = execution.get("resume", "none")
    if resume not in VALID_RESUME_MODES:
        errors.append(
            "execution.resume must be one of: "
            + ", ".join(sorted(VALID_RESUME_MODES))
        )
        resume = "none"
    checkpoint_file = execution.get("checkpoint_file", "checkpoint.json")
    checkpoint_file = relative_path(
        checkpoint_file,
        "execution.checkpoint_file",
        "checkpoint.json",
        errors,
    )
    if checkpoint_file != "checkpoint.json":
        errors.append(
            "execution.checkpoint_file must be exactly checkpoint.json"
        )
    debug_retention_days = execution.get("debug_retention_days", 7)
    if (
        not isinstance(debug_retention_days, int)
        or isinstance(debug_retention_days, bool)
        or not 1 <= debug_retention_days <= 30
    ):
        errors.append(
            "execution.debug_retention_days must be an integer between 1 and 30"
        )
        debug_retention_days = 7
    max_debug_files = execution.get("max_debug_files", 20)
    if (
        not isinstance(max_debug_files, int)
        or isinstance(max_debug_files, bool)
        or not 1 <= max_debug_files <= 50
    ):
        errors.append(
            "execution.max_debug_files must be an integer between 1 and 50"
        )
        max_debug_files = 20
    redact_sensitive = execution.get("redact_sensitive", True)
    if redact_sensitive is not True:
        errors.append("execution.redact_sensitive must be true")
        redact_sensitive = True

    return {
        "limits": limits,
        "artifact_dir": artifact_dir,
        "artifact_mode": artifact_mode,
        "resume": resume,
        "checkpoint_file": checkpoint_file,
        "debug_retention_days": debug_retention_days,
        "max_debug_files": max_debug_files,
        "redact_sensitive": redact_sensitive,
    }


def validate_templates(value: object, errors: list[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("templates must be an object keyed by template id")
        return {}
    for template_id, template in value.items():
        if not non_empty_string(template_id):
            errors.append("template ids must be non-empty strings")
            continue
        if isinstance(template, str):
            require(
                bool(template.strip()),
                f"templates.{template_id} must not be empty",
                errors,
            )
        elif isinstance(template, dict):
            require(
                non_empty_string(template.get("url")),
                f"templates.{template_id}.url is required",
                errors,
            )
        else:
            errors.append(
                f"templates.{template_id} must be a URL string or object"
            )
    return value


def validate_sheet_tabs(sheet: dict, errors: list[str]) -> list[dict]:
    value = sheet.get("tabs")
    if value in (None, []):
        return []
    if not isinstance(value, list):
        errors.append("sheet.tabs must be an array")
        return []
    tabs: list[dict] = []
    names: set[str] = set()
    array_fields = (
        "columns",
        "eligibility_columns",
        "source_columns",
        "target_columns",
    )
    for index, tab in enumerate(value):
        prefix = f"sheet.tabs[{index}]"
        if not isinstance(tab, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = tab.get("name")
        require(non_empty_string(name), f"{prefix}.name is required", errors)
        if non_empty_string(name):
            if name in names:
                errors.append(f"duplicate sheet tab name: {name}")
            names.add(str(name))
        require(
            non_empty_string(tab.get("key")),
            f"{prefix}.key is required",
            errors,
        )
        for field in array_fields:
            if field in tab:
                require(
                    isinstance(tab[field], list)
                    and all(non_empty_string(item) for item in tab[field]),
                    f"{prefix}.{field} must be a string array",
                    errors,
                )
        tabs.append(tab)
    return tabs


def validate_destinations(
    value: object, templates: dict, doc_mode: str, errors: list[str]
) -> tuple[list[dict], set[str]]:
    if value is None:
        return [], set()
    if not isinstance(value, list):
        errors.append("destinations must be an array")
        return [], set()

    destinations: list[dict] = []
    ids: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"destinations[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        destination_id = item.get("id")
        destination_type = item.get("type")
        require(
            non_empty_string(destination_id),
            f"{prefix}.id is required",
            errors,
        )
        if non_empty_string(destination_id):
            if destination_id in ids:
                errors.append(f"duplicate destination id: {destination_id}")
            ids.add(str(destination_id))
        require(
            destination_type in VALID_DESTINATION_TYPES,
            f"{prefix}.type must be sheet or document",
            errors,
        )
        template_ref = item.get("template_ref")
        if template_ref is not None:
            require(
                non_empty_string(template_ref),
                f"{prefix}.template_ref must be a non-empty string",
                errors,
            )
            if non_empty_string(template_ref):
                require(
                    template_ref in templates,
                    f"{prefix}.template_ref does not exist: {template_ref}",
                    errors,
                )
        if destination_type == "document" and doc_mode == "create" and templates:
            require(
                non_empty_string(template_ref),
                f"{prefix}.template_ref is required for document creation",
                errors,
            )
        if destination_type == "sheet":
            require(
                non_empty_string(item.get("platform")),
                f"{prefix}.platform is required for a sheet destination",
                errors,
            )
            require(
                non_empty_string(item.get("sheet_name")),
                f"{prefix}.sheet_name is required for a sheet destination",
                errors,
            )
            field_map = item.get("field_map")
            require(
                isinstance(field_map, dict) and bool(field_map),
                f"{prefix}.field_map must be a non-empty semantic-to-column object",
                errors,
            )
            if isinstance(field_map, dict):
                targets: list[str] = []
                for semantic, target in field_map.items():
                    require(
                        non_empty_string(semantic) and non_empty_string(target),
                        f"{prefix}.field_map keys and values must be non-empty strings",
                        errors,
                    )
                    if non_empty_string(target):
                        targets.append(str(target))
                require(
                    len(targets) == len(set(targets)),
                    f"{prefix}.field_map must not map multiple semantics to one column",
                    errors,
                )
            target_fields = item.get("target_fields")
            if target_fields is not None:
                require(
                    isinstance(target_fields, list)
                    and bool(target_fields)
                    and all(non_empty_string(value) for value in target_fields),
                    f"{prefix}.target_fields must be a non-empty string array",
                    errors,
                )
                if isinstance(target_fields, list) and isinstance(field_map, dict):
                    mapped_names = {
                        str(value)
                        for pair in field_map.items()
                        for value in pair
                        if non_empty_string(value)
                    }
                    for target_field in target_fields:
                        if non_empty_string(target_field):
                            require(
                                target_field in mapped_names,
                                (
                                    f"{prefix}.target_fields value "
                                    f"{target_field} is not in field_map"
                                ),
                                errors,
                            )
            accepted_types = item.get("accepted_content_types")
            if accepted_types is not None:
                require(
                    isinstance(accepted_types, list)
                    and bool(accepted_types)
                    and all(non_empty_string(value) for value in accepted_types),
                    f"{prefix}.accepted_content_types must be a non-empty string array",
                    errors,
                )
        destinations.append(item)
    return destinations, ids


def validate_routing_rules(
    value: object,
    routing_policy: object,
    sheet_tabs: list[dict],
    templates: dict,
    destinations: list[dict],
    destination_ids: set[str],
    errors: list[str],
) -> list[dict]:
    if value in (None, []):
        require(
            routing_policy in (None, {}),
            "routing_policy requires routing_rules",
            errors,
        )
        return []
    if not isinstance(value, list):
        errors.append("routing_rules must be an array")
        return []
    require(
        isinstance(routing_policy, dict)
        and routing_policy.get("match") == "exactly_one"
        and routing_policy.get("unmatched") == "blocked"
        and routing_policy.get("multiple") == "blocked",
        (
            "routing_policy must set match=exactly_one, "
            "unmatched=blocked, and multiple=blocked"
        ),
        errors,
    )
    destination_by_id = {
        str(item.get("id")): item
        for item in destinations
        if non_empty_string(item.get("id"))
    }
    tab_by_name = {
        str(tab.get("name")): tab
        for tab in sheet_tabs
        if non_empty_string(tab.get("name"))
    }
    rules: list[dict] = []
    ids: set[str] = set()
    for index, rule in enumerate(value):
        prefix = f"routing_rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        rule_id = rule.get("id")
        require(non_empty_string(rule_id), f"{prefix}.id is required", errors)
        if non_empty_string(rule_id):
            if rule_id in ids:
                errors.append(f"duplicate routing rule id: {rule_id}")
            ids.add(str(rule_id))
        condition = rule.get("when")
        if not isinstance(condition, dict):
            errors.append(f"{prefix}.when must be an object")
        else:
            require(
                non_empty_string(condition.get("field")),
                f"{prefix}.when.field is required",
                errors,
            )
            has_equals = "equals" in condition
            has_in = "in" in condition
            require(
                has_equals != has_in,
                f"{prefix}.when must contain exactly one of equals or in",
                errors,
            )
            if has_equals:
                require(
                    isinstance(
                        condition.get("equals"),
                        (str, int, float, bool),
                    ),
                    f"{prefix}.when.equals must be a scalar",
                    errors,
                )
            if has_in:
                require(
                    isinstance(condition.get("in"), list)
                    and bool(condition["in"])
                    and all(
                        isinstance(item, (str, int, float, bool))
                        for item in condition["in"]
                    ),
                    f"{prefix}.when.in must be a non-empty scalar array",
                    errors,
                )
            condition_field = condition.get("field")
            tab_refs = rule.get("tab_refs")
            if tab_refs is not None:
                require(
                    isinstance(tab_refs, list)
                    and bool(tab_refs)
                    and all(non_empty_string(ref) for ref in tab_refs),
                    f"{prefix}.tab_refs must be a non-empty string array",
                    errors,
                )
                if isinstance(tab_refs, list):
                    for tab_ref in tab_refs:
                        if non_empty_string(tab_ref):
                            require(
                                tab_ref in tab_by_name,
                                f"{prefix}.tab_refs does not exist: {tab_ref}",
                                errors,
                            )
            if sheet_tabs and non_empty_string(condition_field):
                scoped_tabs = (
                    [
                        tab_by_name[ref]
                        for ref in tab_refs
                        if ref in tab_by_name
                    ]
                    if isinstance(tab_refs, list)
                    else sheet_tabs
                )
                field_presence: list[bool] = []
                for tab in scoped_tabs:
                    declared_fields = {
                        str(tab.get("key") or ""),
                        *(
                            str(item)
                            for name in (
                                "columns",
                                "eligibility_columns",
                                "source_columns",
                                "target_columns",
                            )
                            for item in (tab.get(name) or [])
                        ),
                    }
                    field_presence.append(
                        str(condition_field) in declared_fields
                    )
                require(
                    (
                        all(field_presence)
                        if isinstance(tab_refs, list)
                        else any(field_presence)
                    ),
                    (
                        f"{prefix}.when.field {condition_field} is not "
                        "declared by the routed input tab(s)"
                    ),
                    errors,
                )
        template_ref = rule.get("template_ref")
        if template_ref is not None:
            require(
                non_empty_string(template_ref)
                and template_ref in templates,
                f"{prefix}.template_ref must reference templates",
                errors,
            )
        refs = rule.get("destination_refs")
        require(
            isinstance(refs, list)
            and bool(refs)
            and all(non_empty_string(ref) for ref in refs),
            f"{prefix}.destination_refs must be a non-empty string array",
            errors,
        )
        if isinstance(refs, list):
            for ref in refs:
                if non_empty_string(ref):
                    require(
                        ref in destination_ids,
                        f"{prefix}.destination_refs does not exist: {ref}",
                        errors,
                    )
        content_types = rule.get("content_types")
        if content_types is not None:
            require(
                isinstance(content_types, list)
                and bool(content_types)
                and all(non_empty_string(item) for item in content_types),
                f"{prefix}.content_types must be a non-empty string array",
                errors,
            )
        if isinstance(refs, list):
            for ref in refs:
                destination = destination_by_id.get(str(ref), {})
                destination_template = destination.get("template_ref")
                if (
                    destination.get("type") == "document"
                    and non_empty_string(template_ref)
                    and non_empty_string(destination_template)
                ):
                    require(
                        template_ref == destination_template,
                        (
                            f"{prefix}.template_ref {template_ref} conflicts "
                            f"with destination {ref} template_ref "
                            f"{destination_template}"
                        ),
                        errors,
                    )
                accepted = destination.get("accepted_content_types")
                if isinstance(accepted, list) and accepted:
                    require(
                        isinstance(content_types, list)
                        and bool(content_types),
                        (
                            f"{prefix}.content_types is required by "
                            f"destination {ref}"
                        ),
                        errors,
                    )
                    if isinstance(content_types, list):
                        for content_type in content_types:
                            require(
                                content_type in accepted,
                                (
                                    f"{prefix}.content_type {content_type} "
                                    f"is not accepted by destination {ref}"
                                ),
                                errors,
                            )
        rules.append(rule)
    return rules


def validate_stage_state(value: object, field: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for stage, status in value.items():
        if not non_empty_string(stage):
            errors.append(f"{field} keys must be non-empty strings")
        if status not in VALID_STAGE_STATES:
            errors.append(
                f"{field}.{stage} must be one of: "
                + ", ".join(sorted(VALID_STAGE_STATES))
            )


def validate_entities(
    value: object,
    templates: dict,
    destination_ids: set[str],
    destinations: list[dict],
    errors: list[str],
) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("entities must be an array")
        return []

    source_keys: set[str] = set()
    destination_by_id = {
        str(item.get("id")): item
        for item in destinations
        if non_empty_string(item.get("id"))
    }
    for index, item in enumerate(value):
        prefix = f"entities[{index}]"
        if isinstance(item, str):
            source_key = item
            require(
                bool(source_key.strip()),
                f"{prefix} must not be empty",
                errors,
            )
            require(
                not destination_ids,
                (
                    f"{prefix} must be an object with destination_refs "
                    "when destinations are declared"
                ),
                errors,
            )
        elif isinstance(item, dict):
            raw_source_key = (
                item["source_key"]
                if "source_key" in item
                else item.get("key")
            )
            source_key = (
                raw_source_key
                if isinstance(raw_source_key, str)
                else ""
            )
            require(
                isinstance(raw_source_key, str)
                and bool(raw_source_key.strip()),
                f"{prefix}.source_key must be a non-empty string",
                errors,
            )
            if "join_key" in item:
                require(
                    non_empty_string(item["join_key"]),
                    f"{prefix}.join_key must be a non-empty string",
                    errors,
                )
            template_ref = item.get("template_ref")
            if template_ref is not None:
                require(
                    non_empty_string(template_ref),
                    f"{prefix}.template_ref must be a non-empty string",
                    errors,
                )
                if non_empty_string(template_ref):
                    require(
                        template_ref in templates,
                        f"{prefix}.template_ref does not exist: {template_ref}",
                        errors,
                    )
            if len(templates) > 1:
                require(
                    non_empty_string(template_ref),
                    (
                        f"{prefix}.template_ref is required when multiple "
                        "templates are declared"
                    ),
                    errors,
                )
            refs = item.get("destination_refs", [])
            require(
                isinstance(refs, list)
                and (bool(refs) if destination_ids else True)
                and all(non_empty_string(ref) for ref in refs),
                (
                    f"{prefix}.destination_refs must be a non-empty string "
                    "array when destinations are declared"
                ),
                errors,
            )
            if isinstance(refs, list):
                for ref in refs:
                    if non_empty_string(ref):
                        require(
                            ref in destination_ids,
                            f"{prefix}.destination_refs does not exist: {ref}",
                            errors,
                        )
            content_type = item.get("content_type")
            if content_type is not None:
                require(
                    non_empty_string(content_type),
                    f"{prefix}.content_type must be a non-empty string",
                    errors,
                )
            if isinstance(refs, list):
                for ref in refs:
                    destination = destination_by_id.get(str(ref), {})
                    accepted = destination.get("accepted_content_types")
                    if isinstance(accepted, list) and accepted:
                        require(
                            non_empty_string(content_type),
                            (
                                f"{prefix}.content_type is required by "
                                f"destination {ref}"
                            ),
                            errors,
                        )
                        if non_empty_string(content_type):
                            require(
                                content_type in accepted,
                                (
                                    f"{prefix}.content_type {content_type} is not "
                                    f"accepted by destination {ref}"
                                ),
                                errors,
                            )
            validate_stage_state(
                item.get("stage_state"), f"{prefix}.stage_state", errors
            )
        else:
            errors.append(f"{prefix} must be a string or object")
            continue

        if source_key:
            if source_key in source_keys:
                errors.append(f"duplicate entity source_key: {source_key}")
            source_keys.add(source_key)
    return value


def validate_state(
    state: dict, destination_ids: set[str], errors: list[str]
) -> bool:
    has_unresolved_destination = False
    checkpoint_status = state.get("checkpoint_status")
    if checkpoint_status is not None:
        require(
            checkpoint_status in VALID_CHECKPOINT_STATUSES,
            "state.checkpoint_status must be new or resuming",
            errors,
        )
    validate_stage_state(state.get("stage_state"), "state.stage_state", errors)
    skill_snapshot = state.get("skill_snapshot")
    if skill_snapshot is not None:
        if not isinstance(skill_snapshot, dict):
            errors.append("state.skill_snapshot must be an object")
        else:
            for key in ("commit", "hash"):
                if key in skill_snapshot:
                    require(
                        isinstance(skill_snapshot[key], str),
                        f"state.skill_snapshot.{key} must be a string",
                        errors,
                    )
            if non_empty_string(skill_snapshot.get("hash")):
                require(
                    valid_sha256(skill_snapshot.get("hash")),
                    (
                        "state.skill_snapshot.hash must be a canonical "
                        "lowercase SHA-256"
                    ),
                    errors,
                )
    adapter_versions = state.get("adapter_versions")
    if adapter_versions is not None:
        require(
            isinstance(adapter_versions, dict)
            and all(
                non_empty_string(key) and non_empty_string(value)
                for key, value in adapter_versions.items()
            ),
            "state.adapter_versions must be a string-to-string object",
            errors,
        )
    source_snapshot = state.get("source_snapshot")
    if source_snapshot is not None:
        if not isinstance(source_snapshot, dict):
            errors.append("state.source_snapshot must be an object")
        else:
            for key in ("ref", "hash", "observed_at"):
                require(
                    isinstance(source_snapshot.get(key), str),
                    f"state.source_snapshot.{key} must be a string",
                    errors,
                )
    attempts = state.get("attempts")
    if attempts is not None:
        require(
            isinstance(attempts, dict)
            and all(
                non_empty_string(key)
                and key in VALID_MODULES
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for key, value in attempts.items()
            ),
            (
                "state.attempts must map known module names to "
                "non-negative integers"
            ),
            errors,
        )
    frozen_source_keys = state.get("frozen_source_keys")
    if frozen_source_keys is not None:
        require(
            isinstance(frozen_source_keys, list)
            and all(non_empty_string(key) for key in frozen_source_keys)
            and len(frozen_source_keys) == len(set(frozen_source_keys)),
            (
                "state.frozen_source_keys must be an exact, unique, "
                "non-empty string array"
            ),
            errors,
        )
    records = state.get("records")
    record_keys: set[str] = set()
    if records is not None:
        if not isinstance(records, list):
            errors.append("state.records must be an array")
        else:
            for index, record in enumerate(records):
                prefix = f"state.records[{index}]"
                if not isinstance(record, dict):
                    errors.append(f"{prefix} must be an object")
                    continue
                source_key = record.get("source_key")
                require(
                    non_empty_string(source_key),
                    f"{prefix}.source_key must be a non-empty string",
                    errors,
                )
                if isinstance(source_key, str):
                    require(
                        source_key not in record_keys,
                        f"duplicate checkpoint record source_key: {source_key}",
                        errors,
                    )
                    record_keys.add(source_key)
                required_record_fields = {
                    "source_key",
                    "join_key",
                    "source_ref",
                    "eligibility",
                    "fields",
                    "evidence",
                    "stage_state",
                    "destinations",
                    "verified_outputs",
                    "errors",
                }
                for field in sorted(required_record_fields):
                    require(
                        field in record,
                        f"{prefix} requires canonical field {field}",
                        errors,
                    )
                require(
                    non_empty_string(record.get("join_key")),
                    f"{prefix}.join_key must be a non-empty string",
                    errors,
                )
                source_ref = record.get("source_ref")
                require(
                    isinstance(source_ref, dict)
                    and bool(source_ref)
                    and all(
                        non_empty_string(key)
                        and isinstance(value, str)
                        for key, value in source_ref.items()
                    )
                    and any(
                        non_empty_string(value)
                        for value in source_ref.values()
                    ),
                    (
                        f"{prefix}.source_ref must be a non-empty "
                        "string-to-string object with one non-empty value"
                    ),
                    errors,
                )
                eligibility = record.get("eligibility")
                require(
                    isinstance(eligibility, dict)
                    and eligibility.get("status")
                    in VALID_ELIGIBILITY_STATUSES
                    and isinstance(eligibility.get("reason", ""), str),
                    (
                        f"{prefix}.eligibility requires status "
                        "eligible, skipped, or blocked and a string reason"
                    ),
                    errors,
                )
                require(
                    isinstance(record.get("fields"), dict),
                    f"{prefix}.fields must be an object",
                    errors,
                )
                evidence = record.get("evidence")
                require(
                    isinstance(evidence, list),
                    f"{prefix}.evidence must be an array",
                    errors,
                )
                if isinstance(evidence, list):
                    for evidence_index, item in enumerate(evidence):
                        evidence_prefix = (
                            f"{prefix}.evidence[{evidence_index}]"
                        )
                        require(
                            isinstance(item, dict)
                            and non_empty_string(item.get("observed_at"))
                            and (
                                non_empty_string(item.get("url"))
                                or non_empty_string(item.get("source_id"))
                            ),
                            (
                                f"{evidence_prefix} requires observed_at "
                                "and url or source_id"
                            ),
                            errors,
                        )
                record_stage_state = record.get("stage_state")
                require(
                    isinstance(record_stage_state, dict)
                    and bool(record_stage_state),
                    f"{prefix}.stage_state must be a non-empty object",
                    errors,
                )
                validate_stage_state(
                    record_stage_state,
                    f"{prefix}.stage_state",
                    errors,
                )
                record_destinations = record.get("destinations")
                require(
                    isinstance(record_destinations, dict),
                    f"{prefix}.destinations must be an object",
                    errors,
                )
                if isinstance(record_destinations, dict):
                    for destination_id, destination_value in (
                        record_destinations.items()
                    ):
                        destination_prefix = (
                            f"{prefix}.destinations.{destination_id}"
                        )
                        require(
                            destination_id in destination_ids,
                            (
                                f"{destination_prefix} does not reference "
                                "a declared destination"
                            ),
                            errors,
                        )
                        require(
                            isinstance(destination_value, dict)
                            and destination_value.get("status")
                            in VALID_STAGE_STATES,
                            (
                                f"{destination_prefix} requires a valid "
                                "status"
                            ),
                            errors,
                        )
                require(
                    isinstance(record.get("verified_outputs"), dict),
                    f"{prefix}.verified_outputs must be an object",
                    errors,
                )
                require(
                    isinstance(record.get("errors"), list),
                    f"{prefix}.errors must be an array",
                    errors,
                )
    if isinstance(frozen_source_keys, list) and isinstance(records, list):
        require(
            set(frozen_source_keys) == record_keys
            and len(frozen_source_keys) == len(records),
            (
                "state.records source keys must exactly cover "
                "state.frozen_source_keys"
            ),
            errors,
        )
    verified_outputs = state.get("verified_outputs")
    if verified_outputs is not None:
        require(
            isinstance(verified_outputs, dict)
            and all(non_empty_string(key) for key in verified_outputs),
            "state.verified_outputs must be an object with non-empty string keys",
            errors,
        )
    for key in ("resume_from", "checkpoint_hash", "contract_hash"):
        if key in state:
            require(
                isinstance(state[key], str),
                f"state.{key} must be a string",
                errors,
            )
    if "unexplained_mismatches" in state:
        require(
            isinstance(state["unexplained_mismatches"], list),
            "state.unexplained_mismatches must be an array",
            errors,
        )
    destination_state = state.get("destination_state")
    if destination_state is not None:
        if not isinstance(destination_state, dict):
            errors.append("state.destination_state must be an object")
        else:
            for destination_id, value in destination_state.items():
                prefix = f"state.destination_state.{destination_id}"
                require(
                    destination_id in destination_ids,
                    f"{prefix} does not reference a declared destination",
                    errors,
                )
                if not isinstance(value, dict):
                    errors.append(f"{prefix} must be an object")
                    continue
                status = value.get("status")
                require(
                    status in VALID_STAGE_STATES,
                    f"{prefix}.status must be a valid stage state",
                    errors,
                )
                writer_count = value.get("writer_count", 0)
                require(
                    isinstance(writer_count, int)
                    and not isinstance(writer_count, bool)
                    and writer_count in {0, 1},
                    f"{prefix}.writer_count must be 0 or 1",
                    errors,
                )
                for key in ("writer_owner", "idempotency_key"):
                    if key in value:
                        require(
                            non_empty_string(value[key]),
                            f"{prefix}.{key} must be a non-empty string",
                            errors,
                        )
                process_state = value.get("process_state")
                if process_state is not None:
                    require(
                        process_state in {"running", "ended", "unknown"},
                        (
                            f"{prefix}.process_state must be running, ended, "
                            "or unknown"
                        ),
                        errors,
                    )
                if "actual_state_checked" in value:
                    require(
                        isinstance(value["actual_state_checked"], bool),
                        f"{prefix}.actual_state_checked must be a boolean",
                        errors,
                    )
                if writer_count == 1:
                    require(
                        non_empty_string(value.get("writer_owner")),
                        f"{prefix} active writer requires writer_owner",
                        errors,
                    )
                    require(
                        non_empty_string(value.get("idempotency_key")),
                        f"{prefix} active writer requires idempotency_key",
                        errors,
                    )
                    require(
                        process_state in {"running", "ended", "unknown"},
                        f"{prefix} active writer requires process_state",
                        errors,
                    )
                if status == "running":
                    has_unresolved_destination = True
                    require(
                        writer_count == 1,
                        f"{prefix} running status requires exactly one writer lock",
                        errors,
                    )
                    require(
                        process_state == "running",
                        f"{prefix} running status requires process_state=running",
                        errors,
                    )
                elif status == "unknown":
                    has_unresolved_destination = True
                    require(
                        writer_count == 1,
                        f"{prefix} unknown status requires exactly one writer lock",
                        errors,
                    )
                    require(
                        non_empty_string(value.get("writer_owner")),
                        f"{prefix} unknown status requires writer_owner",
                        errors,
                    )
                    require(
                        non_empty_string(value.get("idempotency_key")),
                        f"{prefix} unknown status requires idempotency_key",
                        errors,
                    )
                    require(
                        process_state in {"running", "ended", "unknown"},
                        f"{prefix} unknown status requires process_state",
                        errors,
                    )
                    require(
                        isinstance(value.get("actual_state_checked"), bool),
                        (
                            f"{prefix} unknown status requires "
                            "actual_state_checked boolean"
                        ),
                        errors,
                    )
                elif status == "verified":
                    require(
                        writer_count == 0,
                        f"{prefix} verified status requires writer_count=0",
                        errors,
                    )
                    require(
                        value.get("actual_state_checked") is True,
                        (
                            f"{prefix} verified status requires "
                            "actual_state_checked=true"
                        ),
                        errors,
                    )
                    require(
                        process_state == "ended",
                        (
                            f"{prefix} verified status requires "
                            "process_state=ended"
                        ),
                        errors,
                    )
                else:
                    require(
                        writer_count == 0,
                        (
                            f"{prefix} {status} status cannot hold "
                            "an active writer lock"
                        ),
                        errors,
                    )
                    require(
                        process_state in {None, "ended"},
                        (
                            f"{prefix} {status} status cannot retain "
                            "a running or unknown process"
                        ),
                        errors,
                    )

    stage_state = state.get("stage_state")
    verified_outputs = state.get("verified_outputs")
    if isinstance(stage_state, dict) and isinstance(verified_outputs, dict):
        mutation_stages = {"document", "sheet_create", "sheet_writeback"}
        for stage, status in stage_state.items():
            if status != "verified":
                continue
            if stage in mutation_stages or stage == "verify":
                require(
                    stage in verified_outputs
                    and valid_readback_proof(
                        verified_outputs.get(stage)
                    ),
                    (
                        f"state.stage_state.{stage}=verified requires "
                        f"a non-empty state.verified_outputs.{stage} proof"
                    ),
                    errors,
                )
            if isinstance(records, list):
                for index, record in enumerate(records):
                    if isinstance(record, dict):
                        record_stage_state = record.get("stage_state")
                        require(
                            isinstance(record_stage_state, dict)
                            and record_stage_state.get(stage) == "verified",
                            (
                                f"state.records[{index}].stage_state.{stage} "
                                "must be verified when the global stage is verified"
                            ),
                            errors,
                        )
                        if stage in mutation_stages or stage == "verify":
                            record_verified_outputs = record.get(
                                "verified_outputs"
                            )
                            require(
                                isinstance(record_verified_outputs, dict)
                                and stage in record_verified_outputs
                                and valid_readback_proof(
                                    record_verified_outputs.get(stage)
                                ),
                                (
                                    f"state.records[{index}].verified_outputs."
                                    f"{stage} must contain a non-empty proof"
                                ),
                                errors,
                            )
    if isinstance(destination_state, dict) and isinstance(verified_outputs, dict):
        for destination_id, value in destination_state.items():
            if not isinstance(value, dict):
                continue
            status = value.get("status")
            targeted_record_states = []
            if isinstance(records, list):
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    record_destinations = record.get("destinations")
                    if (
                        isinstance(record_destinations, dict)
                        and destination_id in record_destinations
                        and isinstance(
                            record_destinations[destination_id], dict
                        )
                    ):
                        targeted_record_states.append(
                            record_destinations[destination_id].get("status")
                        )
            if status in {"running", "unknown", "verified"}:
                require(
                    bool(targeted_record_states),
                    (
                        f"state.destination_state.{destination_id}={status} "
                        "requires at least one matching record destination"
                    ),
                    errors,
                )
            if status == "verified":
                require(
                    destination_id in verified_outputs
                    and valid_readback_proof(
                        verified_outputs.get(destination_id)
                    ),
                    (
                        f"verified destination {destination_id} requires "
                        "a non-empty state.verified_outputs proof"
                    ),
                    errors,
                )
                require(
                    all(
                        record_status == "verified"
                        for record_status in targeted_record_states
                    ),
                    (
                        f"verified destination {destination_id} requires "
                        "all targeted record destinations to be verified"
                    ),
                    errors,
                )
                if isinstance(records, list):
                    for index, record in enumerate(records):
                        if not isinstance(record, dict):
                            continue
                        record_destinations = record.get("destinations")
                        if not (
                            isinstance(record_destinations, dict)
                            and destination_id in record_destinations
                        ):
                            continue
                        record_verified_outputs = record.get(
                            "verified_outputs"
                        )
                        require(
                            isinstance(record_verified_outputs, dict)
                            and destination_id in record_verified_outputs
                            and valid_readback_proof(
                                record_verified_outputs.get(destination_id)
                            ),
                            (
                                f"state.records[{index}].verified_outputs."
                                f"{destination_id} requires a non-empty "
                                "readback proof"
                            ),
                            errors,
                        )
            elif status in {"running", "unknown"}:
                require(
                    status in targeted_record_states,
                    (
                        f"state.destination_state.{destination_id}={status} "
                        "requires a matching record status"
                    ),
                    errors,
                )
    return has_unresolved_destination


def planned_module_chain(
    sheet_mode: str,
    research_enabled: bool,
    doc_mode: str,
    write_mode: str,
    verification: dict,
) -> list[str]:
    chain = ["scope"]
    if sheet_mode == "existing":
        chain.append("sheet_context")
    elif sheet_mode == "create":
        chain.append("sheet_schema")
    if research_enabled:
        chain.append("web_research")
    if (
        research_enabled
        or doc_mode != "none"
        or write_mode != "none"
        or sheet_mode == "create"
    ):
        chain.append("normalize")
    if doc_mode != "none":
        chain.append("document")
    if sheet_mode == "create":
        chain.append("sheet_create")
    if write_mode != "none":
        chain.append("sheet_writeback")
    if (
        doc_mode != "none"
        or sheet_mode == "create"
        or write_mode != "none"
        or verification.get("readback")
        or verification.get("visual")
        or verification.get("coverage")
    ):
        chain.append("verify")
    return chain


def validate_checkpoint_contract(
    state: dict,
    resume_mode: str,
    declared_version: object,
    expected_contract_hash: str,
    expected_checkpoint_hash: str,
    planned_chain: list[str],
    errors: list[str],
) -> None:
    checkpoint_status = state.get("checkpoint_status")
    if resume_mode != "checkpoint":
        require(
            checkpoint_status != "resuming",
            "state.checkpoint_status=resuming requires execution.resume=checkpoint",
            errors,
        )
        return

    require(
        declared_version == SCHEMA_VERSION,
        "execution.resume=checkpoint requires schema_version=3.0",
        errors,
    )
    require(
        checkpoint_status in VALID_CHECKPOINT_STATUSES,
        (
            "execution.resume=checkpoint requires "
            "state.checkpoint_status=new or resuming"
        ),
        errors,
    )
    required_fields = {
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
    for field in sorted(required_fields):
        require(
            field in state,
            f"checkpoint state requires state.{field}",
            errors,
        )

    if checkpoint_status == "new":
        require(
            state.get("contract_hash") in {"", expected_contract_hash},
            "new checkpoint state.contract_hash must be empty or canonical",
            errors,
        )
        require(
            state.get("checkpoint_hash") in {"", expected_checkpoint_hash},
            "new checkpoint state.checkpoint_hash must be empty or canonical",
            errors,
        )
        for field in (
            "stage_state",
            "attempts",
            "frozen_source_keys",
            "records",
            "destination_state",
            "verified_outputs",
            "unexplained_mismatches",
        ):
            require(
                not state.get(field),
                (
                    f"new checkpoint state.{field} must be empty; "
                    "use checkpoint_status=resuming after work starts"
                ),
                errors,
            )
        require(
            state.get("resume_from") == "",
            (
                "new checkpoint state.resume_from must be empty; "
                "use checkpoint_status=resuming after work starts"
            ),
            errors,
        )
        source_snapshot = state.get("source_snapshot")
        require(
            isinstance(source_snapshot, dict)
            and not any(source_snapshot.values()),
            (
                "new checkpoint state.source_snapshot must be empty; "
                "use checkpoint_status=resuming after source acquisition"
            ),
            errors,
        )
        return

    require(
        non_empty_string(state.get("contract_hash")),
        "resuming checkpoint requires state.contract_hash",
        errors,
    )
    require(
        state.get("contract_hash") == expected_contract_hash,
        "resuming checkpoint state.contract_hash does not match the current job",
        errors,
    )
    skill_snapshot = state.get("skill_snapshot")
    require(
        isinstance(skill_snapshot, dict)
        and valid_sha256(skill_snapshot.get("hash")),
        "resuming checkpoint requires state.skill_snapshot.hash",
        errors,
    )
    require(
        isinstance(state.get("adapter_versions"), dict)
        and bool(state.get("adapter_versions")),
        "resuming checkpoint requires non-empty state.adapter_versions",
        errors,
    )
    source_snapshot = state.get("source_snapshot")
    require(
        isinstance(source_snapshot, dict)
        and all(
            non_empty_string(source_snapshot.get(key))
            for key in ("ref", "observed_at")
        ),
        (
            "resuming checkpoint requires state.source_snapshot "
            "ref and observed_at"
        ),
        errors,
    )
    require(
        isinstance(source_snapshot, dict)
        and valid_sha256(source_snapshot.get("hash")),
        (
            "resuming checkpoint requires a canonical SHA-256 "
            "state.source_snapshot.hash"
        ),
        errors,
    )
    require(
        isinstance(state.get("stage_state"), dict)
        and bool(state.get("stage_state")),
        "resuming checkpoint requires non-empty state.stage_state",
        errors,
    )
    require(
        isinstance(state.get("attempts"), dict)
        and bool(state.get("attempts")),
        "resuming checkpoint requires non-empty state.attempts",
        errors,
    )
    require(
        isinstance(state.get("frozen_source_keys"), list)
        and bool(state.get("frozen_source_keys")),
        "resuming checkpoint requires non-empty state.frozen_source_keys",
        errors,
    )
    require(
        isinstance(state.get("records"), list)
        and bool(state.get("records")),
        "resuming checkpoint requires non-empty state.records",
        errors,
    )
    require(
        state.get("resume_from") in VALID_MODULES,
        "resuming checkpoint requires a known state.resume_from module",
        errors,
    )
    stage_state = state.get("stage_state")
    if isinstance(stage_state, dict):
        first_unverified = next(
            (
                module
                for module in planned_chain
                if stage_state.get(module) != "verified"
            ),
            None,
        )
        require(
            first_unverified is not None,
            "resuming checkpoint has no unverified stage",
            errors,
        )
        require(
            state.get("resume_from") == first_unverified,
            (
                "state.resume_from must be the first unverified planned "
                f"stage: {first_unverified}"
            ),
            errors,
        )
        if first_unverified in planned_chain:
            first_index = planned_chain.index(first_unverified)
            for module in planned_chain[first_index + 1 :]:
                require(
                    stage_state.get(module) != "verified",
                    (
                        f"state.stage_state.{module} cannot be verified "
                        f"after earlier unverified stage {first_unverified}"
                    ),
                    errors,
                )
    require(
        non_empty_string(state.get("checkpoint_hash")),
        "resuming checkpoint requires state.checkpoint_hash",
        errors,
    )
    require(
        state.get("checkpoint_hash") == expected_checkpoint_hash,
        "resuming checkpoint state.checkpoint_hash failed integrity validation",
        errors,
    )


def validate_write_policy(
    intent: object,
    existing_values: object,
    write_mode: object,
    selection: object,
    prefix: str,
    errors: list[str],
) -> None:
    require(
        intent in VALID_WRITE_INTENTS - {"audit_only"},
        (
            f"{prefix}.intent must be fill_missing, refresh_existing, "
            "merge_existing, append, or upsert"
        ),
        errors,
    )
    require(
        existing_values in VALID_EXISTING_VALUE_POLICIES,
        f"{prefix}.existing_values must be preserve, overwrite, or merge",
        errors,
    )
    if intent == "refresh_existing":
        require(
            selection in {"all_eligible", "named"}
            and existing_values == "overwrite"
            and write_mode == "fill",
            (
                f"{prefix} refresh_existing requires mode=fill, selection "
                "all_eligible/named, and existing_values=overwrite"
            ),
            errors,
        )
    elif intent == "fill_missing":
        require(
            selection in {"blank_only", "named"}
            and existing_values == "preserve"
            and write_mode == "fill",
            (
                f"{prefix} fill_missing requires mode=fill, selection "
                "blank_only/named, and existing_values=preserve"
            ),
            errors,
        )
    elif intent == "merge_existing":
        require(
            existing_values == "merge" and write_mode == "fill",
            (
                f"{prefix} merge_existing requires mode=fill and "
                "existing_values=merge"
            ),
            errors,
        )
    elif intent == "append":
        require(
            write_mode == "append",
            f"{prefix} append intent requires writeback.mode=append",
            errors,
        )
    elif intent == "upsert":
        require(
            write_mode == "upsert",
            f"{prefix} upsert intent requires writeback.mode=upsert",
            errors,
        )


def validate_field_policies(
    value: object,
    target_columns: object,
    write_mode: object,
    selection: object,
    errors: list[str],
) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("writeback.field_policies must be an object")
        return {}
    targets = set(target_columns) if isinstance(target_columns, list) else set()
    for field, policy in value.items():
        prefix = f"writeback.field_policies.{field}"
        require(
            non_empty_string(field) and field in targets,
            f"{prefix} must reference writeback.target_columns",
            errors,
        )
        if not isinstance(policy, dict):
            errors.append(f"{prefix} must be an object")
            continue
        validate_write_policy(
            policy.get("intent"),
            policy.get("existing_values"),
            write_mode,
            policy.get("selection", selection),
            prefix,
            errors,
        )
    return value


def compose(job: dict) -> dict:
    if not isinstance(job, dict):
        return invalid_result(["job manifest must be an object"])

    errors: list[str] = []
    diagnostics: list[dict] = []
    objective = job.get("objective")
    scope = object_section(job, "scope", errors)
    sheet = object_section(job, "sheet", errors)
    research = object_section(job, "research", errors)
    documents = object_section(job, "documents", errors)
    writeback = object_section(job, "writeback", errors)
    verification = object_section(job, "verification", errors)
    tools = object_section(job, "tools", errors)
    execution = object_section(job, "execution", errors)
    state = object_section(job, "state", errors)

    declared_version = job.get("schema_version")
    if declared_version is not None:
        require(
            declared_version in {"2.0", SCHEMA_VERSION},
            f"schema_version must be 2.0 or {SCHEMA_VERSION}",
            errors,
        )
        if declared_version == "2.0":
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "SCHEMA_V2_COMPAT",
                    "message": (
                        "Schema 2.0 is accepted for migration, but resumable "
                        "side effects require the 3.0 scope/write/coverage contract."
                    ),
                }
            )

    sheet_mode = sheet.get("mode", "none")
    doc_mode = documents.get("mode", "none")
    write_mode = writeback.get("mode", "none")
    research_enabled = bool(research.get("enabled", False))
    sheet_tabs = validate_sheet_tabs(sheet, errors)

    require(
        isinstance(objective, str) and bool(objective.strip()),
        "objective is required",
        errors,
    )
    if "job_id" in job:
        require(isinstance(job["job_id"], str), "job_id must be a string", errors)
    require(
        isinstance(sheet_mode, str) and sheet_mode in VALID_SHEET_MODES,
        f"invalid sheet.mode: {sheet_mode}",
        errors,
    )
    require(
        isinstance(doc_mode, str) and doc_mode in VALID_DOC_MODES,
        f"invalid documents.mode: {doc_mode}",
        errors,
    )
    require(
        isinstance(write_mode, str) and write_mode in VALID_WRITE_MODES,
        f"invalid writeback.mode: {write_mode}",
        errors,
    )
    if "platform" in sheet:
        require(
            isinstance(sheet["platform"], str),
            "sheet.platform must be a string",
            errors,
        )

    if sheet_mode == "existing":
        require(
            non_empty_string(sheet.get("url")),
            "sheet.url is required for an existing sheet",
            errors,
        )
    if sheet_mode == "create":
        require(
            non_empty_string(sheet.get("platform")),
            "sheet.platform is required when creating a sheet",
            errors,
        )
    if write_mode != "none":
        require(
            sheet_mode != "none",
            "writeback requires an existing or new sheet",
            errors,
        )
        require(
            non_empty_string(sheet.get("key"))
            or (
                bool(sheet_tabs)
                and all(non_empty_string(tab.get("key")) for tab in sheet_tabs)
            ),
            "sheet.key or a key on every sheet.tabs entry is required for writeback",
            errors,
        )

    if "enabled" in research:
        require(
            isinstance(research["enabled"], bool),
            "research.enabled must be a boolean",
            errors,
        )
    if research_enabled:
        fields = research.get("fields")
        require(
            isinstance(fields, list)
            and bool(fields)
            and all(non_empty_string(item) for item in fields),
            "research.fields must be a non-empty string array when research is enabled",
            errors,
        )
    if "sources" in research:
        sources = research["sources"]
        require(
            isinstance(sources, list)
            and all(non_empty_string(item) for item in sources),
            "research.sources must be a string array",
            errors,
        )

    templates = validate_templates(job.get("templates"), errors)
    destinations, destination_ids = validate_destinations(
        job.get("destinations"), templates, str(doc_mode), errors
    )
    entities = validate_entities(
        job.get("entities"), templates, destination_ids, destinations, errors
    )
    routing_rules = validate_routing_rules(
        job.get("routing_rules"),
        job.get("routing_policy"),
        sheet_tabs,
        templates,
        destinations,
        destination_ids,
        errors,
    )
    if not entities and (
        len(destination_ids) > 1 or len(templates) > 1
    ):
        require(
            bool(routing_rules),
            (
                "routing_rules are required for discovered entities with "
                "multiple templates or destinations"
            ),
            errors,
        )

    if doc_mode != "none":
        require(
            non_empty_string(documents.get("system")),
            "documents.system is required",
            errors,
        )
        if doc_mode == "create":
            single_template = documents.get("template")
            require(
                non_empty_string(single_template) or bool(templates),
                "document creation requires documents.template or templates",
                errors,
            )
            if templates:
                document_destinations = [
                    item
                    for item in destinations
                    if item.get("type") == "document"
                ]
                require(
                    bool(document_destinations),
                    "templates require at least one document destination",
                    errors,
                )
        validate_document_provider(tools, documents, errors)

    if any(item.get("type") == "document" for item in destinations):
        require(
            doc_mode != "none",
            "document destinations require documents.mode create or update",
            errors,
        )

    selection = scope.get("selection")
    eligibility_source = scope.get("eligibility_source")
    source_key_policy = scope.get("source_key_policy")
    write_intent = writeback.get("intent")
    existing_values = writeback.get("existing_values")
    field_policies: dict = {}
    if write_mode != "none":
        require(
            selection in VALID_SCOPE_SELECTIONS,
            "scope.selection must be all_eligible, blank_only, or named for writeback",
            errors,
        )
        require(
            non_empty_string(eligibility_source),
            "scope.eligibility_source is required for writeback",
            errors,
        )
        require(
            source_key_policy in VALID_SOURCE_KEY_POLICIES,
            "scope.source_key_policy must be exact_structured for writeback",
            errors,
        )
        target_columns = writeback.get("target_columns")
        require(
            isinstance(target_columns, list)
            and bool(target_columns)
            and all(non_empty_string(item) for item in target_columns),
            "writeback.target_columns must be a non-empty string array",
            errors,
        )
        validate_write_policy(
            write_intent,
            existing_values,
            write_mode,
            selection,
            "writeback",
            errors,
        )
        field_policies = validate_field_policies(
            writeback.get("field_policies"),
            target_columns,
            write_mode,
            selection,
            errors,
        )
        if isinstance(target_columns, list):
            sheet_destinations = [
                item
                for item in destinations
                if item.get("type") == "sheet"
            ]
            covered_targets: set[str] = set()
            for destination in sheet_destinations:
                field_map = destination.get("field_map")
                if not isinstance(field_map, dict):
                    continue
                mapped_names = {
                    str(value)
                    for pair in field_map.items()
                    for value in pair
                    if non_empty_string(value)
                }
                destination_targets = destination.get(
                    "target_fields", target_columns
                )
                if not isinstance(destination_targets, list):
                    continue
                for target_column in destination_targets:
                    if not non_empty_string(target_column):
                        continue
                    require(
                        target_column in target_columns,
                        (
                            f"destination {destination.get('id')} target "
                            f"{target_column} is not in writeback.target_columns"
                        ),
                        errors,
                    )
                    require(
                        target_column in mapped_names,
                        (
                            f"writeback target {target_column} is not "
                            f"mapped by destination {destination.get('id')}"
                        ),
                        errors,
                    )
                    covered_targets.add(str(target_column))
            if sheet_destinations:
                for target_column in target_columns:
                    if non_empty_string(target_column):
                        require(
                            target_column in covered_targets,
                            (
                                f"writeback target {target_column} is not "
                                "assigned to any sheet destination"
                            ),
                            errors,
                        )
        require(
            verification.get("coverage") is True,
            "verification.coverage must be true for writeback",
            errors,
        )
        if selection == "named":
            require(
                bool(entities),
                "named scope requires at least one entity",
                errors,
            )
    elif write_intent is not None:
        require(
            write_intent == "audit_only",
            "writeback.intent must be audit_only when writeback.mode=none",
            errors,
        )
        require(
            writeback.get("field_policies") in (None, {}),
            "writeback.field_policies must be empty when writeback.mode=none",
            errors,
        )

    if documents.get("audience") is not None:
        require(
            documents.get("audience") in {"internal", "external"},
            "documents.audience must be internal or external",
            errors,
        )
    if documents.get("content_policy") is not None:
        require(
            documents.get("content_policy")
            in {"preserve", "deliverable_only"},
            "documents.content_policy must be preserve or deliverable_only",
            errors,
        )
    if (
        doc_mode != "none"
        and documents.get("audience") == "external"
    ):
        require(
            documents.get("content_policy") == "deliverable_only",
            "external documents require documents.content_policy=deliverable_only",
            errors,
        )

    for key in ("readback", "visual", "coverage"):
        if key in verification:
            require(
                isinstance(verification[key], bool),
                f"verification.{key} must be a boolean",
                errors,
            )
    if (
        write_mode != "none"
        or doc_mode != "none"
        or sheet_mode == "create"
    ):
        require(
            verification.get("readback") is True,
            "verification.readback must be true for every side effect",
            errors,
        )

    for key in (
        "browser_skill",
        "sheet_skill",
        "bilibili_script",
        "xingtu_skill",
    ):
        if key in tools:
            require(
                non_empty_string(tools[key]),
                f"tools.{key} must be a non-empty string",
                errors,
            )

    execution_contract = resolve_execution(execution, errors)
    expected_contract_hash = contract_hash(job)
    expected_checkpoint_hash = checkpoint_hash(job)
    planned_chain = planned_module_chain(
        str(sheet_mode),
        research_enabled,
        str(doc_mode),
        str(write_mode),
        verification,
    )
    has_unresolved_destination = validate_state(
        state, destination_ids, errors
    )
    if (
        execution_contract["resume"] == "checkpoint"
        and state.get("checkpoint_status") == "resuming"
        and entities
    ):
        entity_source_keys: set[str] = set()
        for item in entities:
            source_key = (
                item
                if isinstance(item, str)
                else item.get("source_key", item.get("key"))
                if isinstance(item, dict)
                else None
            )
            if isinstance(source_key, str):
                entity_source_keys.add(source_key)
        require(
            set(state.get("frozen_source_keys", []))
            == entity_source_keys,
            (
                "state.frozen_source_keys must exactly match explicit "
                "entity source keys"
            ),
            errors,
        )
        records_by_key = {
            record.get("source_key"): record
            for record in state.get("records", [])
            if isinstance(record, dict)
            and isinstance(record.get("source_key"), str)
        }
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            source_key = entity.get("source_key", entity.get("key"))
            record = records_by_key.get(source_key)
            if not isinstance(record, dict):
                continue
            record_destinations = record.get("destinations")
            require(
                isinstance(record_destinations, dict)
                and set(record_destinations)
                == set(entity.get("destination_refs", [])),
                (
                    f"checkpoint record {source_key} destination keys must "
                    "exactly match the explicit entity destination_refs"
                ),
                errors,
            )
    validate_checkpoint_contract(
        state,
        execution_contract["resume"],
        declared_version,
        expected_contract_hash,
        expected_checkpoint_hash,
        planned_chain,
        errors,
    )

    if execution_contract["resume"] == "checkpoint":
        require(
            bool(job.get("job_id")),
            "job_id is required when execution.resume=checkpoint",
            errors,
        )
    if has_unresolved_destination:
        require(
            execution_contract["resume"] == "checkpoint",
            (
                "running or unknown destination state requires "
                "execution.resume=checkpoint"
            ),
            errors,
        )

    if errors:
        return invalid_result(errors)

    chain = planned_chain
    checkpoints = [
        (
            "🔴 CHECKPOINT: Freeze selection, eligibility source, exact source-key "
            "policy, target fields, existing-value policy, destinations, and "
            "completion coverage before research."
        )
    ]

    if sheet_mode == "existing":
        checkpoints.append(
            "Read live headers, exact source keys, target cells, and formats "
            "for every declared tab."
        )
    elif sheet_mode == "create":
        checkpoints.append(
            "Approve a unique key, columns, types, and blank policy."
        )

    if research_enabled:
        checkpoints.append(
            "Capture requested fields with source URL/ID and observation time; "
            "add screenshots only for UI-only or ambiguous evidence."
        )

    needs_records = "normalize" in chain
    if needs_records:
        checkpoints.append(
            "Keep exact source_key separate from normalized join_key; stop on "
            "duplicates, ambiguity, or unresolved required fields."
        )

    if doc_mode != "none":
        checkpoints.append(
            "🔴 CHECKPOINT: Resolve a ready provider and content policy, then use "
            "one writer and one dry-run/apply/read-back transaction per document."
        )

    if sheet_mode == "create":
        checkpoints.append(
            "Verify the empty schema and baseline formatting before loading data."
        )
    if write_mode != "none":
        checkpoints.append(
            "🔴 CHECKPOINT: Re-read live state, reconcile every eligible key, "
            "then write by exact source_key with fresh preconditions."
        )

    if "verify" in chain:
        checkpoints.append(
            "Verify fresh state and prove eligible_fields = ready_fields + "
            "not_distributed_fields + skipped_fields + blocked_fields with "
            "zero unexplained mismatches."
        )

    checkpoints.append(
        "🛑 STOP: Never replay verified research, documents, or rows. After an "
        "unknown mutation outcome, lock the destination and read actual state "
        "before any retry."
    )

    tool_plan = []
    if sheet.get("platform", "").lower() == "popo" and sheet_mode != "none":
        popo_modules = [
            item
            for item in (
                "sheet_context",
                "sheet_schema",
                "sheet_create",
                "sheet_writeback",
            )
            if item in chain
        ]
        if sheet_mode == "create" or write_mode != "none":
            popo_modules.extend(
                item for item in ("verify",) if item in chain
            )
        tool_plan.append(
            {
                "modules": popo_modules,
                "tools": [
                    tools.get("sheet_skill", "popo-sheet"),
                    tools.get("browser_skill", "kimi-webbridge"),
                ],
                "note": (
                    "Use the POPO adapter contract and one authenticated task "
                    "tab. Do not reconstruct ShareDB/WebSocket operations."
                ),
            }
        )
    if research_enabled:
        sources = " ".join(
            str(value).lower() for value in (research.get("sources") or [])
        )
        is_bilibili = any(
            token in sources for token in ("bilibili", "b站", "哔哩")
        )
        is_xingtu = any(
            token in sources for token in ("douyin", "抖音", "xingtu", "星图")
        )
        research_tools = []
        if is_bilibili:
            research_tools.append(
                tools.get("bilibili_script", "scripts/bilibili_batch.py")
            )
        if is_xingtu:
            research_tools.append(
                tools.get("xingtu_skill", "douyin-xingtu")
            )
        if is_bilibili or not research_tools:
            research_tools.append(
                tools.get("browser_skill", "kimi-webbridge")
            )
        tool_plan.append(
            {
                "modules": ["web_research"],
                "tools": research_tools,
                "note": (
                    "Use the dedicated batch adapter first and send only "
                    "unresolved or login-dependent records to its bounded fallback."
                ),
            }
        )

    provider_resolution = {}
    if doc_mode != "none":
        resolution, provider_diagnostics = document_resolution(tools, documents)
        provider_resolution["document"] = resolution
        diagnostics.extend(provider_diagnostics)
        tool_plan.append(
            {
                "modules": ["document"],
                "tools": [f"document-provider:{resolution['platform']}"],
                "provider_ref": None,
                "note": (
                    "Run the portable provider preflight; never serialize "
                    "commands, paths, or credentials into the job plan."
                ),
            }
        )

    run_contract = {
        "selection": selection or "not_applicable",
        "eligibility_source": eligibility_source or "",
        "source_key_policy": source_key_policy or "exact_structured",
        "write_intent": write_intent or "audit_only",
        "existing_values": existing_values or "not_applicable",
        "target_columns": writeback.get("target_columns", []),
        "field_policies": field_policies,
        "input_tabs": [
            {
                "name": tab.get("name"),
                "key": tab.get("key"),
                "eligibility_columns": tab.get(
                    "eligibility_columns", []
                ),
                "source_columns": tab.get("source_columns", []),
                "target_columns": tab.get("target_columns", []),
            }
            for tab in sheet_tabs
        ],
        "routing_rules": routing_rules,
        "routing_policy": job.get("routing_policy", {}),
        "destination_ids": sorted(destination_ids),
        "destination_field_maps": {
            str(item.get("id")): {
                "target_fields": item.get(
                    "target_fields", writeback.get("target_columns", [])
                ),
                "field_map": item.get("field_map", {}),
            }
            for item in destinations
            if item.get("type") == "sheet"
        },
        "coverage_equation": (
            "eligible_fields = ready_fields + not_distributed_fields + "
            "skipped_fields + blocked_fields"
        ),
        "verification_scope": (
            "all_eligible" if verification.get("coverage") else "planned_outputs"
        ),
        "one_writer_per_destination": True,
    }
    resume_contract = {
        "mode": execution_contract["resume"],
        "checkpoint": (
            f"{execution_contract['artifact_dir']}/"
            f"{execution_contract['checkpoint_file']}"
        ).replace("\\", "/"),
        "checkpoint_status": state.get("checkpoint_status", ""),
        "contract_hash": expected_contract_hash,
        "atomic_writer": "scripts/checkpoint_artifacts.py",
        "verified_work_is_immutable": True,
        "unknown_outcome_action": "read_actual_state_before_retry",
        "required_state": [
            "checkpoint_status",
            "contract_hash",
            "skill_snapshot",
            "adapter_versions",
            "source_snapshot",
            "stage_state",
            "frozen_source_keys",
            "records",
            "attempts",
            "destination_state",
            "verified_outputs",
            "resume_from",
            "checkpoint_hash",
            "unexplained_mismatches",
        ],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "valid": True,
        "runnable": doc_mode == "none" and not has_unresolved_destination,
        "job_id": job.get("job_id", ""),
        "chain": chain,
        "checkpoints": checkpoints,
        "tool_plan": tool_plan,
        "provider_resolution": provider_resolution,
        "run_contract": run_contract,
        "resume_contract": resume_contract,
        "diagnostics": diagnostics,
        "artifact_dir": execution_contract["artifact_dir"],
        "artifact_mode": execution_contract["artifact_mode"],
        "artifact_policy": {
            "helper": "scripts/checkpoint_artifacts.py",
            "normal_allowlist": [
                execution_contract["checkpoint_file"],
                "change-plan.json",
                "verification-summary.json",
            ],
            "debug_retention_days": execution_contract[
                "debug_retention_days"
            ],
            "max_debug_files": execution_contract["max_debug_files"],
            "redact_sensitive": execution_contract["redact_sensitive"],
        },
        "execution_limits": execution_contract["limits"],
    }


def self_test() -> None:
    def canonical_record(
        source_key: str,
        *,
        stage_state: dict | None = None,
        destinations: dict | None = None,
        verified_outputs: dict | None = None,
    ) -> dict:
        return {
            "source_key": source_key,
            "join_key": source_key,
            "source_ref": {"type": "explicit_user"},
            "eligibility": {"status": "eligible", "reason": ""},
            "fields": {},
            "evidence": [],
            "stage_state": stage_state or {"scope": "verified"},
            "destinations": destinations or {},
            "verified_outputs": verified_outputs or {},
            "errors": [],
        }

    def new_checkpoint_state() -> dict:
        return {
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
        }

    assert not compose([])["valid"]  # type: ignore[arg-type]
    malformed = compose({"objective": "test", "sheet": ["not-an-object"]})
    assert not malformed["valid"]
    assert "sheet must be an object" in malformed["errors"]

    invalid_entities = compose(
        {
            "objective": "test",
            "entities": "not-a-list",
            "sheet": {"mode": "none"},
            "documents": {"mode": "none"},
            "research": {"enabled": False},
            "writeback": {"mode": "none"},
        }
    )
    assert not invalid_entities["valid"]
    assert "entities must be an array" in invalid_entities["errors"]

    exact_key_manifest = {
        "objective": "preserve exact keys",
        "entities": ["A", " A "],
        "sheet": {"mode": "none"},
        "documents": {"mode": "none"},
        "research": {"enabled": False},
        "writeback": {"mode": "none"},
    }
    assert compose(exact_key_manifest)["valid"]

    numeric_source_key = json.loads(json.dumps(exact_key_manifest))
    numeric_source_key["entities"] = [{"source_key": 123}]
    assert not compose(numeric_source_key)["valid"]

    base_document = {
        "schema_version": "3.0",
        "objective": "test",
        "entities": [],
        "sheet": {"mode": "none"},
        "research": {"enabled": False},
        "writeback": {"mode": "none"},
        "verification": {"readback": True},
        "tools": {
            "document_provider": {"platform": "feishu", "mode": "auto"}
        },
        "documents": {
            "mode": "update",
            "system": "feishu",
            "permission": "anyone_editable",
        },
    }
    result = compose(base_document)
    required = result["provider_resolution"]["document"][
        "required_capabilities"
    ]
    assert result["valid"] and not result["runnable"]
    assert "permission.public.read" in required
    assert "permission.public.anyone_editable" in required
    assert "permission.write" not in required

    invalid_permission = json.loads(json.dumps(base_document))
    invalid_permission["documents"]["permission"] = "organization_editable"
    assert not compose(invalid_permission)["valid"]

    invalid_provider = json.loads(json.dumps(base_document))
    invalid_provider["tools"]["document_provider"]["mode"] = "surprise"
    assert not compose(invalid_provider)["valid"]

    disabled_readback = json.loads(json.dumps(base_document))
    disabled_readback["verification"]["readback"] = False
    assert not compose(disabled_readback)["valid"]

    multi_destination = {
        "schema_version": "3.0",
        "job_id": "mixed-docs",
        "objective": "Create two routed documents",
        "entities": [
            {
                "source_key": "A",
                "template_ref": "brief",
                "destination_refs": ["briefs"],
            },
            {
                "source_key": "B",
                "template_ref": "review",
                "destination_refs": ["reviews"],
            },
        ],
        "templates": {
            "brief": {"url": "https://example.test/brief"},
            "review": {"url": "https://example.test/review"},
        },
        "destinations": [
            {"id": "briefs", "type": "document", "template_ref": "brief"},
            {"id": "reviews", "type": "document", "template_ref": "review"},
        ],
        "sheet": {"mode": "none"},
        "research": {"enabled": False},
        "documents": {
            "mode": "create",
            "system": "feishu",
            "permission": "none",
            "audience": "external",
            "content_policy": "deliverable_only",
        },
        "writeback": {"mode": "none"},
        "verification": {"readback": True},
        "execution": {
            "resume": "checkpoint",
            "checkpoint_file": "checkpoint.json",
        },
        "state": new_checkpoint_state(),
    }
    mixed_result = compose(multi_destination)
    assert mixed_result["valid"], mixed_result["errors"]
    assert mixed_result["resume_contract"]["mode"] == "checkpoint"

    dynamic_routing = json.loads(json.dumps(multi_destination))
    dynamic_routing["entities"] = []
    dynamic_routing["routing_policy"] = {
        "match": "exactly_one",
        "unmatched": "blocked",
        "multiple": "blocked",
    }
    dynamic_routing["routing_rules"] = [
        {
            "id": "brief-project",
            "when": {"field": "project_type", "equals": "brief"},
            "template_ref": "brief",
            "destination_refs": ["briefs"],
        },
        {
            "id": "review-project",
            "when": {"field": "project_type", "equals": "review"},
            "template_ref": "review",
            "destination_refs": ["reviews"],
        },
    ]
    dynamic_result = compose(dynamic_routing)
    assert dynamic_result["valid"], dynamic_result["errors"]
    assert len(dynamic_result["run_contract"]["routing_rules"]) == 2

    missing_dynamic_rules = json.loads(json.dumps(dynamic_routing))
    del missing_dynamic_rules["routing_rules"]
    assert not compose(missing_dynamic_rules)["valid"]

    bad_dynamic_template = json.loads(json.dumps(dynamic_routing))
    bad_dynamic_template["routing_rules"][0]["template_ref"] = "missing"
    assert not compose(bad_dynamic_template)["valid"]

    conflicting_dynamic_template = json.loads(json.dumps(dynamic_routing))
    conflicting_dynamic_template["routing_rules"][0][
        "template_ref"
    ] = "review"
    assert not compose(conflicting_dynamic_template)["valid"]

    duplicate_entity = json.loads(json.dumps(multi_destination))
    duplicate_entity["entities"][1]["source_key"] = "A"
    assert not compose(duplicate_entity)["valid"]

    invalid_destination = json.loads(json.dumps(multi_destination))
    invalid_destination["destinations"][1]["id"] = "briefs"
    assert not compose(invalid_destination)["valid"]

    refresh = {
        "schema_version": "3.0",
        "job_id": "refresh",
        "objective": "Refresh all eligible metrics",
        "entities": [],
        "scope": {
            "selection": "all_eligible",
            "eligibility_source": "sheet status and platform columns",
            "source_key_policy": "exact_structured",
        },
        "sheet": {
            "mode": "existing",
            "platform": "popo",
            "url": "https://example.test/sheet",
            "key": "creator",
        },
        "research": {
            "enabled": True,
            "fields": ["views"],
            "sources": ["Bilibili"],
        },
        "documents": {"mode": "none"},
        "writeback": {
            "mode": "fill",
            "intent": "refresh_existing",
            "existing_values": "overwrite",
            "target_columns": ["views"],
        },
        "verification": {
            "readback": True,
            "visual": False,
            "coverage": True,
        },
        "execution": {
            "artifact_mode": "minimal",
            "resume": "checkpoint",
            "checkpoint_file": "checkpoint.json",
        },
        "state": new_checkpoint_state(),
    }
    refresh_result = compose(refresh)
    assert refresh_result["valid"], refresh_result["errors"]
    assert refresh_result["run_contract"]["existing_values"] == "overwrite"
    assert refresh_result["run_contract"]["verification_scope"] == "all_eligible"
    assert refresh_result["artifact_policy"]["normal_allowlist"] == [
        "checkpoint.json",
        "change-plan.json",
        "verification-summary.json",
    ]

    schema2_resumable = json.loads(json.dumps(refresh))
    schema2_resumable["schema_version"] = "2.0"
    assert not compose(schema2_resumable)["valid"]

    missing_checkpoint_state = json.loads(json.dumps(refresh))
    del missing_checkpoint_state["state"]["source_snapshot"]
    assert not compose(missing_checkpoint_state)["valid"]

    progressed_as_new = json.loads(json.dumps(refresh))
    progressed_as_new["state"]["stage_state"] = {"scope": "verified"}
    assert not compose(progressed_as_new)["valid"]

    resumed = json.loads(json.dumps(refresh))
    resumed["state"] = {
        "checkpoint_status": "resuming",
        "contract_hash": "",
        "skill_snapshot": {
            "commit": "working-tree",
            "hash": "sha256:" + "1" * 64,
        },
        "adapter_versions": {"bilibili_batch": "1.0"},
        "source_snapshot": {
            "ref": "input/sheet-snapshot.json",
            "hash": "sha256:" + "2" * 64,
            "observed_at": "2026-07-27T12:00:00+08:00",
        },
        "stage_state": {
            "scope": "verified",
            "sheet_context": "verified",
        },
        "attempts": {
            "scope": 1,
            "sheet_context": 1,
            "web_research": 1,
        },
        "frozen_source_keys": ["creator-1"],
        "records": [
            canonical_record(
                "creator-1",
                stage_state={
                    "scope": "verified",
                    "sheet_context": "verified",
                },
            )
        ],
        "destination_state": {},
        "verified_outputs": {"scope": {"status": "verified"}},
        "resume_from": "web_research",
        "checkpoint_hash": "",
        "unexplained_mismatches": [],
    }
    resumed["state"]["contract_hash"] = contract_hash(resumed)
    resumed["state"]["checkpoint_hash"] = checkpoint_hash(resumed)
    assert compose(resumed)["valid"]

    changed_contract = json.loads(json.dumps(resumed))
    changed_contract["objective"] = "Changed after checkpoint"
    assert not compose(changed_contract)["valid"]

    tampered_checkpoint = json.loads(json.dumps(resumed))
    tampered_checkpoint["state"]["records"][0]["source_key"] = "creator-2"
    assert not compose(tampered_checkpoint)["valid"]

    shallow_record = json.loads(json.dumps(resumed))
    shallow_record["state"]["records"] = [{"source_key": "creator-1"}]
    shallow_record["state"]["checkpoint_hash"] = checkpoint_hash(shallow_record)
    assert not compose(shallow_record)["valid"]

    discovered_frozen_keys = json.loads(json.dumps(resumed))
    discovered_frozen_keys["state"]["frozen_source_keys"] = ["creator-2"]
    discovered_frozen_keys["state"]["records"] = [
        canonical_record(
            "creator-2",
            stage_state={
                "scope": "verified",
                "sheet_context": "verified",
            },
        )
    ]
    discovered_frozen_keys["state"]["checkpoint_hash"] = checkpoint_hash(
        discovered_frozen_keys
    )
    # Sheet-discovered jobs have no explicit entity list to match.
    assert compose(discovered_frozen_keys)["valid"]

    resume_without_mode = json.loads(json.dumps(resumed))
    resume_without_mode["execution"]["resume"] = "none"
    assert not compose(resume_without_mode)["valid"]

    skips_earlier_pending = json.loads(json.dumps(resumed))
    skips_earlier_pending["state"]["resume_from"] = "normalize"
    skips_earlier_pending["state"]["checkpoint_hash"] = checkpoint_hash(
        skips_earlier_pending
    )
    assert not compose(skips_earlier_pending)["valid"]

    custom_checkpoint_name = json.loads(json.dumps(refresh))
    custom_checkpoint_name["execution"]["checkpoint_file"] = "resume.json"
    assert not compose(custom_checkpoint_name)["valid"]

    mixed_sources = json.loads(json.dumps(refresh))
    mixed_sources["research"]["sources"] = ["Bilibili", "Douyin Xingtu"]
    mixed_sources["sheet"]["tabs"] = [
        {
            "name": "B站",
            "key": "达人昵称",
            "eligibility_columns": ["状态"],
            "source_columns": ["视频链接"],
            "target_columns": ["播放量"],
        },
        {
            "name": "抖音",
            "key": "达人昵称",
            "eligibility_columns": ["状态"],
            "source_columns": ["作品链接"],
            "target_columns": ["播放量"],
        },
    ]
    mixed_source_result = compose(mixed_sources)
    mixed_tools = {
        tool
        for item in mixed_source_result["tool_plan"]
        for tool in item["tools"]
    }
    assert "scripts/bilibili_batch.py" in mixed_tools
    assert "douyin-xingtu" in mixed_tools
    assert len(mixed_source_result["run_contract"]["input_tabs"]) == 2

    duplicate_tab = json.loads(json.dumps(mixed_sources))
    duplicate_tab["sheet"]["tabs"][1]["name"] = "B站"
    assert not compose(duplicate_tab)["valid"]

    missing_tab_key = json.loads(json.dumps(mixed_sources))
    missing_tab_key["sheet"]["tabs"][1]["key"] = ""
    assert not compose(missing_tab_key)["valid"]

    missing_policy = json.loads(json.dumps(refresh))
    del missing_policy["writeback"]["existing_values"]
    assert not compose(missing_policy)["valid"]

    wrong_intent = json.loads(json.dumps(refresh))
    wrong_intent["writeback"]["intent"] = "fill_missing"
    assert not compose(wrong_intent)["valid"]

    contradictory = json.loads(json.dumps(refresh))
    contradictory["scope"]["selection"] = "blank_only"
    assert not compose(contradictory)["valid"]

    typed_destination = json.loads(json.dumps(refresh))
    typed_destination["destinations"] = [
        {
            "id": "customer-bilibili",
            "type": "sheet",
            "platform": "popo",
            "sheet_name": "B站",
            "field_map": {
                "main_link": "发布链接",
                "distribution_link": "分发链接",
                "play_count": "播放量",
            },
            "accepted_content_types": ["video"],
        }
    ]
    typed_destination["entities"] = [
        {
            "source_key": "上海滩许Van强",
            "content_type": "video",
            "destination_refs": ["customer-bilibili"],
        }
    ]
    typed_destination["writeback"]["target_columns"] = ["play_count"]
    assert compose(typed_destination)["valid"]

    typed_rule = json.loads(json.dumps(typed_destination))
    typed_rule["entities"] = []
    typed_rule["routing_policy"] = {
        "match": "exactly_one",
        "unmatched": "blocked",
        "multiple": "blocked",
    }
    typed_rule["sheet"]["tabs"] = [
        {
            "name": "B站",
            "key": "达人昵称",
            "source_columns": ["content_type", "视频链接"],
            "eligibility_columns": ["状态"],
            "target_columns": ["播放量"],
        }
    ]
    typed_rule["routing_rules"] = [
        {
            "id": "video-only",
            "when": {"field": "content_type", "equals": "video"},
            "tab_refs": ["B站"],
            "destination_refs": ["customer-bilibili"],
            "content_types": ["video"],
        }
    ]
    assert compose(typed_rule)["valid"]

    rejected_rule_type = json.loads(json.dumps(typed_rule))
    rejected_rule_type["routing_rules"][0]["content_types"] = ["live"]
    assert not compose(rejected_rule_type)["valid"]

    unknown_route_field = json.loads(json.dumps(typed_rule))
    unknown_route_field["routing_rules"][0]["when"]["field"] = "content_typo"
    assert not compose(unknown_route_field)["valid"]

    unknown_route_tab = json.loads(json.dumps(typed_rule))
    unknown_route_tab["routing_rules"][0]["tab_refs"] = ["missing-tab"]
    assert not compose(unknown_route_tab)["valid"]

    mixed_field_policies = json.loads(json.dumps(typed_destination))
    mixed_field_policies["writeback"]["target_columns"] = [
        "play_count",
        "document_link",
    ]
    mixed_field_policies["writeback"]["field_policies"] = {
        "document_link": {
            "intent": "merge_existing",
            "existing_values": "merge",
        }
    }
    mixed_field_policies["destinations"][0]["target_fields"] = [
        "play_count"
    ]
    mixed_field_policies["destinations"].append(
        {
            "id": "brief-links",
            "type": "sheet",
            "platform": "popo",
            "sheet_name": "BRF链接",
            "target_fields": ["document_link"],
            "field_map": {"document_link": "BRF链接"},
        }
    )
    mixed_field_policies["entities"][0]["destination_refs"].append(
        "brief-links"
    )
    mixed_result = compose(mixed_field_policies)
    assert mixed_result["valid"], mixed_result["errors"]
    assert (
        mixed_result["run_contract"]["field_policies"]["document_link"][
            "existing_values"
        ]
        == "merge"
    )

    bad_field_policy = json.loads(json.dumps(mixed_field_policies))
    bad_field_policy["writeback"]["field_policies"]["document_link"][
        "existing_values"
    ] = "preserve"
    assert not compose(bad_field_policy)["valid"]

    unknown_field_policy = json.loads(json.dumps(mixed_field_policies))
    unknown_field_policy["writeback"]["field_policies"]["not-a-target"] = {
        "intent": "merge_existing",
        "existing_values": "merge",
    }
    assert not compose(unknown_field_policy)["valid"]

    duplicate_column = json.loads(json.dumps(typed_destination))
    duplicate_column["destinations"][0]["field_map"][
        "distribution_link"
    ] = "发布链接"
    assert not compose(duplicate_column)["valid"]

    rejected_content_type = json.loads(json.dumps(typed_destination))
    rejected_content_type["entities"][0]["content_type"] = "live"
    assert not compose(rejected_content_type)["valid"]

    missing_content_type = json.loads(json.dumps(typed_destination))
    del missing_content_type["entities"][0]["content_type"]
    assert not compose(missing_content_type)["valid"]

    unmapped_target = json.loads(json.dumps(typed_destination))
    unmapped_target["writeback"]["target_columns"] = ["followers"]
    assert not compose(unmapped_target)["valid"]

    unknown_destination = json.loads(json.dumps(typed_destination))
    unknown_destination["state"] = json.loads(
        json.dumps(resumed["state"])
    )
    unknown_source_key = unknown_destination["entities"][0]["source_key"]
    unknown_destination["state"]["frozen_source_keys"] = [
        unknown_source_key
    ]
    unknown_destination["state"]["records"] = [
        canonical_record(
            unknown_source_key,
            destinations={
                "customer-bilibili": {"status": "unknown"}
            },
        )
    ]
    unknown_destination["state"]["resume_from"] = "sheet_writeback"
    unknown_destination["state"]["stage_state"].update(
        {
            "web_research": "verified",
            "normalize": "verified",
        }
    )
    unknown_destination["state"]["attempts"].update(
        {
            "web_research": 1,
            "normalize": 1,
        }
    )
    unknown_destination["state"]["destination_state"] = {
        "customer-bilibili": {
            "status": "unknown",
            "writer_count": 1,
            "writer_owner": "document-writer-1",
            "idempotency_key": "write-001",
            "process_state": "unknown",
            "actual_state_checked": False,
        }
    }
    unknown_destination["state"]["records"][0]["stage_state"].update(
        {
            "sheet_context": "verified",
            "web_research": "verified",
            "normalize": "verified",
        }
    )
    unknown_destination["state"]["contract_hash"] = contract_hash(
        unknown_destination
    )
    unknown_destination["state"]["checkpoint_hash"] = checkpoint_hash(
        unknown_destination
    )
    unknown_result = compose(unknown_destination)
    assert unknown_result["valid"], unknown_result["errors"]
    assert not unknown_result["runnable"]

    running_locked = json.loads(json.dumps(unknown_destination))
    running_locked["state"]["destination_state"]["customer-bilibili"][
        "status"
    ] = "running"
    running_locked["state"]["destination_state"]["customer-bilibili"][
        "process_state"
    ] = "running"
    running_locked["state"]["records"][0]["destinations"][
        "customer-bilibili"
    ]["status"] = "running"
    running_locked["state"]["checkpoint_hash"] = checkpoint_hash(
        running_locked
    )
    running_result = compose(running_locked)
    assert running_result["valid"] and not running_result["runnable"]

    explicit_key_mismatch = json.loads(json.dumps(unknown_destination))
    explicit_key_mismatch["state"]["frozen_source_keys"] = ["wrong-key"]
    explicit_key_mismatch["state"]["records"] = [
        canonical_record("wrong-key")
    ]
    explicit_key_mismatch["state"]["checkpoint_hash"] = checkpoint_hash(
        explicit_key_mismatch
    )
    assert not compose(explicit_key_mismatch)["valid"]

    running_without_lock = json.loads(json.dumps(unknown_destination))
    running_state = running_without_lock["state"]["destination_state"][
        "customer-bilibili"
    ]
    running_state["status"] = "running"
    running_state["writer_count"] = 0
    running_state["process_state"] = "running"
    running_without_lock["state"]["records"][0]["destinations"][
        "customer-bilibili"
    ]["status"] = "running"
    running_without_lock["state"]["checkpoint_hash"] = checkpoint_hash(
        running_without_lock
    )
    assert not compose(running_without_lock)["valid"]

    verified_without_proof = json.loads(json.dumps(unknown_destination))
    verified_state = verified_without_proof["state"]["destination_state"][
        "customer-bilibili"
    ]
    verified_state.update(
        {
            "status": "verified",
            "writer_count": 0,
            "process_state": "ended",
            "actual_state_checked": True,
        }
    )
    verified_without_proof["state"]["records"][0]["destinations"][
        "customer-bilibili"
    ]["status"] = "verified"
    verified_without_proof["state"]["verified_outputs"] = {}
    verified_without_proof["state"]["checkpoint_hash"] = checkpoint_hash(
        verified_without_proof
    )
    assert not compose(verified_without_proof)["valid"]

    verified_with_proof = json.loads(
        json.dumps(verified_without_proof)
    )
    verified_with_proof["state"]["verified_outputs"][
        "customer-bilibili"
    ] = {
        "readback_hash": "sha256:" + "3" * 64,
        "observed_at": "2026-07-27T12:05:00+08:00",
    }
    verified_with_proof["state"]["records"][0]["verified_outputs"][
        "customer-bilibili"
    ] = {
        "readback_hash": "sha256:" + "3" * 64,
        "observed_at": "2026-07-27T12:05:00+08:00",
    }
    verified_with_proof["state"]["checkpoint_hash"] = checkpoint_hash(
        verified_with_proof
    )
    assert compose(verified_with_proof)["valid"]

    replay_verified_stage = json.loads(json.dumps(resumed))
    replay_verified_stage["state"]["stage_state"][
        "web_research"
    ] = "verified"
    replay_verified_stage["state"]["records"][0]["stage_state"][
        "web_research"
    ] = "verified"
    replay_verified_stage["state"]["resume_from"] = "web_research"
    replay_verified_stage["state"]["checkpoint_hash"] = checkpoint_hash(
        replay_verified_stage
    )
    assert not compose(replay_verified_stage)["valid"]

    unknown_without_process = json.loads(json.dumps(unknown_destination))
    del unknown_without_process["state"]["destination_state"][
        "customer-bilibili"
    ]["process_state"]
    assert not compose(unknown_without_process)["valid"]

    dual_writer = json.loads(json.dumps(unknown_destination))
    dual_writer["state"]["destination_state"]["customer-bilibili"][
        "writer_count"
    ] = 2
    assert not compose(dual_writer)["valid"]

    unknown_without_resume = json.loads(json.dumps(unknown_destination))
    unknown_without_resume["execution"]["resume"] = "none"
    assert not compose(unknown_without_resume)["valid"]

    invalid_artifacts = json.loads(json.dumps(refresh))
    invalid_artifacts["execution"]["artifact_mode"] = "everything"
    assert not compose(invalid_artifacts)["valid"]

    disabled_redaction = json.loads(json.dumps(refresh))
    disabled_redaction["execution"]["redact_sensitive"] = False
    assert not compose(disabled_redaction)["valid"]

    missing_job_id = json.loads(json.dumps(refresh))
    del missing_job_id["job_id"]
    assert not compose(missing_job_id)["valid"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a research-sheet job and compose its bounded module chain."
        )
    )
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
            print(
                f"- {', '.join(item['modules'])}: "
                f"{' + '.join(item['tools'])}"
            )
        for index, item in enumerate(result["checkpoints"], 1):
            print(f"{index}. {item}")
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
