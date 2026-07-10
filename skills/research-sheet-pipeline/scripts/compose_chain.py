#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


VALID_SHEET_MODES = {"none", "existing", "create"}
VALID_DOC_MODES = {"none", "create", "update"}
VALID_WRITE_MODES = {"none", "append", "fill", "upsert"}
DEFAULT_FEISHU_CLI = r"C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools\feishu-doc.ps1"


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def compose(job: dict) -> dict:
    errors: list[str] = []
    objective = job.get("objective")
    sheet = job.get("sheet") or {}
    research = job.get("research") or {}
    documents = job.get("documents") or {}
    writeback = job.get("writeback") or {}
    verification = job.get("verification") or {}
    tools = job.get("tools") or {}

    sheet_mode = sheet.get("mode", "none")
    doc_mode = documents.get("mode", "none")
    write_mode = writeback.get("mode", "none")
    research_enabled = bool(research.get("enabled", False))

    require(isinstance(objective, str) and bool(objective.strip()), "objective is required", errors)
    require(sheet_mode in VALID_SHEET_MODES, f"invalid sheet.mode: {sheet_mode}", errors)
    require(doc_mode in VALID_DOC_MODES, f"invalid documents.mode: {doc_mode}", errors)
    require(write_mode in VALID_WRITE_MODES, f"invalid writeback.mode: {write_mode}", errors)

    if sheet_mode == "existing":
        require(bool(sheet.get("url")), "sheet.url is required for an existing sheet", errors)
    if sheet_mode == "create":
        require(bool(sheet.get("platform")), "sheet.platform is required when creating a sheet", errors)
    if write_mode != "none":
        require(sheet_mode != "none", "writeback requires an existing or new sheet", errors)
        require(bool(sheet.get("key")), "sheet.key is required for writeback", errors)
    if research_enabled:
        require(isinstance(research.get("fields"), list) and bool(research.get("fields")), "research.fields is required when research is enabled", errors)
    if doc_mode != "none":
        require(bool(documents.get("system")), "documents.system is required", errors)
        if doc_mode == "create":
            require(bool(documents.get("template")), "documents.template is required for document creation", errors)

    if errors:
        return {"valid": False, "errors": errors, "chain": [], "tool_plan": []}

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
        checkpoints.append("Capture requested fields with URL and screenshot evidence.")

    needs_records = research_enabled or doc_mode != "none" or write_mode != "none" or sheet_mode == "create"
    if needs_records:
        chain.append("normalize")
        checkpoints.append("Stop on duplicate keys, ambiguous matches, or unresolved required fields.")

    if doc_mode != "none":
        chain.append("document")
        checkpoints.append("Verify document content, link, and requested sharing permission.")

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
        tool_plan.append({
            "modules": [item for item in ("sheet_context", "sheet_schema", "sheet_create", "sheet_writeback", "verify") if item in chain],
            "tools": [tools.get("browser_skill", "kimi-webbridge"), tools.get("sheet_skill", "popo-sheet")],
            "note": "Use Kimi WebBridge as the authenticated Edge transport and popo-sheet as the workbook protocol.",
        })
    if research_enabled:
        tool_plan.append({
            "modules": ["web_research"],
            "tools": [tools.get("browser_skill", "kimi-webbridge")],
            "note": "Use the user's Edge session for social/profile pages and screenshots.",
        })
    if doc_mode != "none":
        document_tool = tools.get("feishu_cli", DEFAULT_FEISHU_CLI) if documents.get("system") == "feishu-cli" else documents.get("system")
        tool_plan.append({
            "modules": ["document"],
            "tools": [document_tool],
            "note": "Run Feishu reads, copies, edits, permissions, and read-back through this CLI only.",
        })

    return {
        "valid": True,
        "job_id": job.get("job_id", ""),
        "chain": chain,
        "checkpoints": checkpoints,
        "tool_plan": tool_plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a research-sheet job and compose its module chain.")
    parser.add_argument("job", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

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
