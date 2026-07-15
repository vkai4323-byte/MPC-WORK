---
name: research-sheet-pipeline
description: Compose efficient, evidence-backed workflows when a task combines spreadsheet context, external entity or content research, and optionally batch document production or sheet writeback. Use for POPO or online-sheet research, Bilibili/Douyin/Xiaohongshu/Kuaishou creator or content data, BRF batches derived from a sheet or research, large name-matched fills, partial refreshes, and new research tables. Do not use for a direct edit to one already-known document when no sheet or external research is needed.
---

# Research Sheet Pipeline

Build the shortest safe chain. Skip supplied, completed, or unnecessary modules, and never repeat research because a downstream transport failed.

## Choose the path

Modules: `scope`, `sheet_context`, `sheet_schema`, `web_research`, `normalize`, `document`, `sheet_create`, `sheet_writeback`, `verify`.

- Existing sheet + refresh/fill: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.
- Existing sheet + documents, no writeback: `sheet_context -> web_research -> normalize -> document -> verify`.
- Existing sheet + documents + links: add `sheet_writeback` after `document`.
- New table: `sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.
- Research only: `web_research -> normalize`.
- Known records only: start at `normalize`.
- Known document follow-up: `document -> verify`. If the URL and edits are already known and no upstream data is needed, use the designated document tool directly rather than re-reading a sheet or re-running research.

### Fast path

Keep the plan in memory and skip the job manifest and `scripts/compose_chain.py` when all are true:

1. At most five entities or documents.
2. One sheet/tab at most, one template family, and one destination.
3. No sheet creation or writeback.
4. Identity, requested fields, edits, permission rule, and completion criteria are clear.

Read the shared sheet/template once, batch independent research, and process independent documents with concurrency 2–3 when the document tool supports it.

Create a manifest from `assets/job-template.json` only for multiple destinations, mixed template families, sheet creation/writeback combined with documents, more than five documents, ambiguous business rules, or a job that must resume across sessions. Run `scripts/compose_chain.py` only for such jobs.

Read `references/tool-routing.md` before selecting tools. Read `references/module-contracts.md` before a document, sheet, permission, or other side effect. When documents are enabled, also read `references/document-providers.md`. Read `references/recipes.md` only when a listed recipe matches. Load the full instructions for every selected skill before action.

## Bound the run

- Preflight a selected route once. For Bilibili, run the script entry point `python scripts/bilibili_batch.py --self-test` with the configured workspace Python. If `python` is a Windows Store shim, resolve the bundled runtime first; do not test a global import that bypasses `.deps`.
- For documents, run `python scripts/document_provider.py --format json`, repeating `--required-capability` for the job's exact capability list. Parse its JSON even when exit code 3 or 4 reports an unfinished preflight. Use a ready CLI or a capability-equivalent authenticated Agent connector. If credentials are missing, instruct the user to configure them locally; never request, print, or persist `FEISHU_APP_SECRET` in chat, manifests, or run artifacts.
- Default budgets: tool preflight 60 seconds, POPO read-only acquisition 90 seconds total, one API batch 120 seconds, and one document transaction 120 seconds. On expiry, stop that route, preserve completed work, and choose at most one stated fallback.
- If a shell call yields a cell ID, call `wait` immediately. Poll for at most 60 seconds at a time; after two no-progress waits, terminate it and use the bounded fallback.
- Do not leave the user without a progress update for more than 60 seconds during active work.
- For a fast-path job, prefer in-memory records. Otherwise store run artifacts under `.codex-runs/research-sheet-pipeline/<job-id>/`; keep only the canonical checkpoint, final change plan, and verification summary. Save debug payloads only on failure or request.
- Return compact tool results: counts, keys/IDs, unmatched items, and errors. Avoid full workbook snapshots or full document block payloads in the conversation; target about 2 KB per entity/document when the tool permits.

## Optimize before research

1. Read only the named tab and the columns needed for filtering, identity, source URL, requested values, and key matching.
2. Filter targets before opening pages or calling APIs.
3. Deduplicate source URLs/IDs and research them in one batch.
4. Use the fastest authoritative route from `references/tool-routing.md`; send only unresolved records to browser fallback.
5. Normalize once, then fan out document work or build one sheet update.
6. Perform one fresh, compact structural read-back per destination.

Default evidence is source URL/ID, observation time, and returned source fields. Do not take one screenshot per entity unless requested, the field is UI-only, or visual evidence is needed to resolve identity/result ambiguity.

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

Prefer the bundled `scripts/bilibili_batch.py` for public Bilibili data:

- Known video URLs/BV/AV IDs: `--input`.
- Keyword video discovery: `--search`.
- Known creator space URLs or UIDs in keyed objects: `--creator-input ... --recent N`.
- Creator-name discovery: `--user-search`, then confirm name plus MID before creator lookup.

Keep concurrency at 2 by default. On `412`, retry once serially, then use the script's bounded user-search fallback; mark incomplete recent uploads as `partial`. Use `$kimi-webbridge` only for unresolved items, login/private state, UI-only fields, or an API failure after the bounded fallback. Never silently convert a whole creator batch into serial browser navigation.

If `--self-test` reports a missing dependency, request permission once to install `requirements.txt`. The script prefers an ignored `.deps` directory when present and otherwise uses the configured Python environment; do not test a different interpreter.

## Gate POPO reads and writes

Treat POPO acquisition, external research, and POPO writeback as independent stages. Preserve canonical research if POPO transport later fails.

For read-only or disposable-login links:

1. Reuse one existing authenticated session and one task tab.
2. If `disposable_login_token=1`, never navigate the same URL a second time. Inspect or refresh the current tab once.
3. Use at most two acquisition attempts and at most one task-created fresh session, within 90 seconds total.
4. Do not cascade evaluate, snapshot, screenshots, and new tabs merely to confirm the same unavailable state.
5. If still unavailable, continue independent research, preserve partial results, and request a renewed link only when sheet data is indispensable.

Before any write, run the write transport gate and bounded recovery in `references/module-contracts.md`. Build current-snapshot writes by live key; never reuse row IDs, column IDs, versions, or old-value preconditions after failure. One exact fresh read-back, not an acknowledgement, establishes completion.

## Process documents as transactions

- Resolve and capability-check the document provider before reading the template. Treat a valid job manifest and a runnable provider as separate gates; never accept a legacy CLI's or connector's claimed capabilities without a local declaration or read-only probe.
- Keep machine-specific commands in per-user config or environment variables. Accept legacy `tools.feishu_cli` as an explicit pinned command, but never publish or echo its path.
- Read a shared template once and cache its structural signature for the run.
- For each document, merge common and entity-specific replacements into one plan; use one dry-run, one grouped apply, and one compact read-back when the tool supports it. The bundled Feishu adapter applies at most 200 affected blocks in one idempotent batch and verifies content plus structure automatically.
- Process independent documents with concurrency 2–3. Keep operations within one document ordered.
- Capability-check non-text operations such as comments, images, permissions, or style changes before copying or editing.
- Stop before mutation when a replacement crosses styled runs and the tool cannot preserve them safely; choose a block-level or style-aware operation rather than flattening the text.
- Verify title, required facts, sharing state, and a structural signature: block type, nesting, order, style/list metadata, and non-text anchors. Equal block counts alone do not prove format preservation.
- If no provider is ready, stop only `document`, preserve normalized records and evidence, and resume from that stage after setup.

## Write safely without repeating work

Immediately before sheet writeback:

1. Pass the platform transport gate.
2. Fetch one fresh structured snapshot and resolve the live tab, headers, keys, row/column IDs, target values, and version.
3. Stop on duplicate keys, shifted headers, read-only targets, or conflicting non-empty values unless overwrite was requested.
4. Submit one batch with version and old-value preconditions.
5. Read back exact planned key/value pairs; keep verified rows out of every retry.
6. Rebuild from fresh state and retry only unexplained mismatches once.

## Verify and report

Completion requires zero unexplained mismatches in requested outputs. Screenshots are presentation checks, not value verification.

Ask only when a missing platform, key, template, permission, duplicate, or overwrite rule would materially change the result. Otherwise infer visible conventions and continue.

Report target count, API successes, browser fallbacks, documents created/updated, sheet rows updated/skipped, verification result, elapsed-stage exceptions, and unresolved items. Keep implementation artifacts out of the final response unless useful for audit.
