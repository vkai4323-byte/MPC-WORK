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

## POPO writeback transport gate and recovery

Treat research and sheet transport as separate stages. Once canonical records are ready, keep their source URL/ID, observed time, normalized values, and unique key in memory or a lightweight local checkpoint. Never rerun source research because POPO transport failed.

### 🔴 CHECKPOINT — preflight before every POPO write

Confirm all of the following in the same editable POPO tab before fetching a ShareDB snapshot:

1. Kimi WebBridge is connected and the tab is the task's current session tab.
2. The POPO page is online and does not show an offline or reconnecting state.
3. The target sheet is editable, not protected, and not a read-only duplicate.
4. The live visible key, headers, target columns, and overwrite policy are still the intended ones.

If any check fails, do not open links, fetch Bilibili again, submit a write, or reuse stale row/column IDs. Record the failed gate and enter the matching recovery branch below.

### Bounded recovery state machine

| Failure signal | Required action | Limit / next state |
|---|---|---|
| WebBridge unavailable or extension disconnected | Start/connect the bridge once, then recheck the same task tab. | If still unavailable, stop writeback and report the bridge blocker. |
| POPO page shows offline/reconnecting | Do not issue a ShareDB op. Recheck the same tab once after a short wait. | If still offline, close only the task-created session and create at most one fresh editable task session; run the full preflight again. If that session is offline too, stop writeback. |
| Snapshot or op timeout while the page is online | Retry the same stage in the same tab after short backoff (for example 1s, then 3s). | At most two attempts per stage. Do not increase timeouts indefinitely or open another window for a transient timeout. |
| Fresh session cannot pass preflight or snapshot read | Preserve canonical records and the planned writes; do not research again. | Stop and report the failed stage, attempts, and the exact action needed to resume. |
| Batch op acknowledgement is uncertain, or read-back fails | Fetch a fresh state before any retry. Compare by live key and exact target value. | Retry only unexplained mismatches once; never replay the whole batch. |

For each writable row, build a current-snapshot write plan containing `key`, `sheetId`, `rowId`, `colId`, `oldValue`, `newValue`, and `snapshotVersion`. Build it immediately before the op; discard it after a transport failure and rebuild from a fresh state. Return only the compact target-cell comparison from browser evaluation, not the full workbook snapshot, to avoid transferring a multi-megabyte workbook through the tool channel.

### Never do these during POPO recovery

- Do not repeatedly open new POPO windows or switch Kimi sessions until one happens to work; use the single controlled fresh-session attempt above.
- Do not treat an offline page as a transient snapshot timeout.
- Do not reuse row IDs, column IDs, snapshot versions, or old-value preconditions from a failed attempt.
- Do not rerun Bilibili/API research, click data hyperlinks, or overwrite unrelated cells while recovering sheet transport.
- Do not claim success from an op acknowledgement alone; require an exact fresh read-back.

## Write safely without repeating work

Immediately before writeback:

1. Pass the POPO transport gate. On failure, follow the bounded recovery state machine before any write attempt.
2. Fetch one fresh structured sheet snapshot and resolve the live tab, headers, row IDs, column IDs, and target cells from visible keys; never reuse external row numbers.
3. Stop on duplicate keys, shifted headers, protected/read-only targets, or conflicting non-empty values unless overwrite was requested.
4. Build the current-snapshot write plan, then submit one batch using its version and old-value preconditions.
5. Fetch a fresh state and compare only exact planned key/value pairs; keep verified rows out of every retry.
6. If verification fails, rebuild the plan and retry only mismatched records once. Never replay already verified writes or completed research.

An API/WebSocket acknowledgment is not completion. Fresh read-back with zero unexplained mismatches is completion. Screenshots are presentation checks, not value verification.

## Checkpoints and reporting

Ask only when a missing platform, key, template, permission, duplicate, or overwrite rule would materially change the result. Otherwise infer visible headers and continue.

Report target count, researched/API-success/browser-fallback count, updated/skipped count, verification result, and unresolved items. Keep implementation artifacts out of the final response unless useful for audit.
