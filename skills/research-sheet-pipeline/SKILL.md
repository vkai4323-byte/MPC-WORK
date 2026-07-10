---
name: research-sheet-pipeline
description: Compose reliable workflows that inspect or create a spreadsheet, research entities in the user's real browser, normalize evidence-backed data, optionally create or update documents, bulk-write results, and verify them. Use for POPO/online spreadsheet operations, Kimi WebBridge research, creator or vendor lists, BRF/document production, large name-matched fills, from-scratch table creation, and any task that needs only a subset of research, document, and sheet stages.
---

# Research Sheet Pipeline

Build the smallest safe chain from reusable modules. Do not force every task through document creation or an existing sheet.

## Start with a job contract

Capture the task in a job manifest before multi-stage work. Copy `assets/job-template.json` or create an equivalent object. For chains of three or more modules, run:

```powershell
python scripts/compose_chain.py job.json
```

Treat the manifest as working state, not user-facing bureaucracy. Infer visible headers and obvious defaults. Ask only when a missing platform, key, or business rule would materially change the output.

## Select modules

Use these module IDs and contracts:

1. `scope`: define objective, entities, fields, target system, and completion criteria.
2. `sheet_context`: read an existing sheet's tabs, headers, key column, target columns, formats, and current values.
3. `sheet_schema`: draft columns, types, key, evidence fields, and link fields when no sheet exists.
4. `web_research`: use the user's authenticated browser to collect requested fields and evidence.
5. `normalize`: produce one canonical record per entity, resolve aliases, and surface conflicts.
6. `document`: optionally create or update documents from a named template and attach returned links to records.
7. `sheet_create`: create the requested workbook/sheet and apply the approved schema and baseline formatting.
8. `sheet_writeback`: append, fill, or upsert records by the in-sheet key, never by external row number.
9. `verify`: read back values, reconcile counts, and visually check presentation when formatting matters.

Read `references/tool-routing.md` before selecting or calling tools. Read `references/module-contracts.md` before executing a chain that writes to a sheet or creates documents. Read `references/recipes.md` when choosing among common branches.

## Route by outcome

- Existing sheet + research + documents + links: `scope -> sheet_context -> web_research -> normalize -> document -> sheet_writeback -> verify`.
- No sheet + research + new table: `scope -> sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.
- Research + bulk fill only: `scope -> sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.
- Research only: `scope -> web_research -> normalize`.
- Documents only from known records: `scope -> normalize -> document -> verify`.
- Existing dataset + new table: `scope -> sheet_schema -> normalize -> sheet_create -> sheet_writeback -> verify`.

Skip modules whose outputs are already supplied and trustworthy. Re-run `sheet_context` immediately before writes if the live sheet may have changed.

## Preserve one canonical record contract

Pass records between modules in this shape:

```json
{
  "key": "visible unique name or ID",
  "aliases": [],
  "fields": {},
  "evidence": [{"url": "", "screenshot": "", "observed_at": ""}],
  "documents": [{"type": "", "title": "", "url": ""}],
  "status": "ready",
  "errors": []
}
```

Keep raw evidence separate from normalized fields. Every writeable record must have a unique key. Do not invent missing values; leave them blank or mark them unresolved according to the target sheet's policy.

## Use the correct execution skills and tools

- For any POPO URL or POPO workbook operation, load both `$popo-sheet` and `$kimi-webbridge`; execute POPO's structured read/write rules through the real authenticated browser session.
- For Douyin, Bilibili, Xiaohongshu, Kuaishou, or another login/session-dependent profile page, load `$kimi-webbridge` and use the user's Edge session for navigation, reading, and screenshots.
- For every Feishu read, copy, edit, permission, and verification operation, use the existing `feishu-doc-tools` package at `C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools\feishu-doc.ps1` from its containing directory. Never substitute OpenClaw or browser editing.
- Use `$spreadsheets` for standalone `.xlsx`, `.csv`, or `.tsv` artifacts.
- Reuse the same browser session per job. Preserve the user's logged-in state and do not open data links incidentally.

Before acting, print or internally record a tool plan that maps every selected module to its concrete skill, executable, or API. Stop if a required fixed tool is unavailable instead of silently substituting another one.

## Enforce checkpoints

Before research, confirm requested fields and evidence requirements. Before document creation, confirm template, naming rule, release node, and per-entity customization inputs. Before writing, stop on duplicate keys, ambiguous matches, protected cells, non-empty conflicting targets, or shifted schemas.

After writing, verify exact values by fresh structured read-back. Compare row counts and keys, then check hyperlinks and formatting. A successful browser action or API acknowledgment is not sufficient without read-back.

## Report by module

State which modules ran, record counts at each boundary, documents created or skipped, rows inserted/updated/skipped, verification result, and unresolved items. Keep source evidence available for audit without flooding the final response.
