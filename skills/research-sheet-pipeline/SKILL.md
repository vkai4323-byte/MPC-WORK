---
name: research-sheet-pipeline
description: Compose fast, reliable workflows that inspect or create a spreadsheet, research entities through APIs or the user's authenticated browser, normalize evidence-backed data, optionally create documents, bulk-write results, and verify them. Use for POPO or online spreadsheet research, Bilibili/Douyin/Xiaohongshu/Kuaishou creator or content data, BRF/document production, large name-matched fills, partial refreshes, and from-scratch research tables.
---

# Research Sheet Pipeline

Build the shortest safe chain. Skip supplied or unnecessary modules.

## Choose the path

Modules: `scope`, `sheet_context`, `sheet_schema`, `web_research`, `normalize`, `document`, `sheet_create`, `sheet_writeback`, `verify`.

- Existing sheet + refresh/fill: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.
- Existing sheet + documents: add `document` before writeback.
- New table: `sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.
- Research only: `web_research -> normalize`.
- Known records only: start at `normalize`.

Do not create a job manifest for a clear, single-destination refresh or fill. Create one from `assets/job-template.json` only when the task has multiple destinations, document generation, ambiguous business rules, or must be resumable. Run `scripts/compose_chain.py` only for such complex jobs.

Read `references/tool-routing.md` before selecting tools. Read `references/module-contracts.md` before a write or document side effect. Read `references/recipes.md` only when a listed recipe matches.

## Optimize before research

1. Read only the named tab and the minimum columns needed for filtering, identity, source URL, target value, and key matching.
2. Filter target rows before opening pages or calling APIs.
3. Deduplicate source URLs/IDs and research them in a batch.
4. Use the fastest authoritative route in `references/tool-routing.md`; send only unresolved records to browser fallback.
5. Write one contiguous or batched update, then perform one fresh structural read-back.

Default evidence is source URL/ID, observation time, and returned source fields. Do not take one screenshot per entity unless the user requests screenshots, the value is UI-only, or identity/result ambiguity needs visual evidence.

## Preserve canonical records

```json
{
  "key": "visible unique name or ID",
  "aliases": [],
  "fields": {},
  "evidence": [{"url": "", "source_id": "", "observed_at": ""}],
  "documents": [],
  "status": "ready",
  "errors": []
}
```

Every writable record needs a unique key. Keep raw evidence separate from normalized fields. Leave missing values blank or unresolved; never invent them.

## Route Bilibili efficiently

For public Bilibili video metadata, play counts, creator metadata, or keyword search, prefer the bundled `scripts/bilibili_batch.py`, which uses `bilibili-api-python` from `kiakun-collab/bilibili-api`.

- Known video URLs/BV/AV IDs: batch them in one invocation.
- Keyword discovery: use the script's search mode, then confirm identity from title, owner, and BVID.
- Keep concurrency low (default 2). On `412`, reduce to serial requests and back off.
- Use `$kimi-webbridge` only for unresolved items, login-dependent/private state, UI-only fields, or API failure after bounded retries.

If `bilibili_api` and an async request backend are missing, request permission to install `bilibili-api-python` plus `httpx`; do not silently switch the whole batch to serial browser navigation.

## Write safely without repeating work

Immediately before writeback:

1. Fetch a fresh structured sheet snapshot with a bounded retry for transient WebSocket failures.
2. Resolve the live tab, headers, row IDs, column IDs, and target cells from visible keys; never reuse external row numbers.
3. Stop on duplicate keys, shifted headers, protected/read-only targets, or conflicting non-empty values unless overwrite was requested.
4. Submit one batch using the current snapshot version and old-value preconditions.
5. Fetch one new snapshot and compare exact key/value pairs.
6. If verification fails, rebuild the plan and retry only mismatched records once. Never replay already verified writes.

An API/WebSocket acknowledgment is not completion. Fresh read-back with zero unexplained mismatches is completion. Screenshots are presentation checks, not value verification.

## Checkpoints and reporting

Ask only when a missing platform, key, template, permission, duplicate, or overwrite rule would materially change the result. Otherwise infer visible headers and continue.

Report target count, researched/API-success/browser-fallback count, updated/skipped count, verification result, and unresolved items. Keep implementation artifacts out of the final response unless useful for audit.
