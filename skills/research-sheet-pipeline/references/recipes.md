# Composition recipes

## Contents

- Fast read-only BRF batch
- Isolated known-document edit
- Simple deterministic sheet batch
- Full metric refresh / blank-only fill
- Multi-tab mixed-platform refresh
- Bilibili / Douyin Xingtu metric refresh
- Master execution sheet to customer platform sheets
- Template documents and link writeback
- Unknown mutation outcome and cross-session resume
- Research to a new table

## Fast read-only BRF batch

Use for 1–5 entities, one sheet/tab, one template family, and no writeback.

Chain: `sheet_context -> web_research -> normalize -> document -> verify`.

1. Keep the frozen contract in memory; do not create a manifest.
2. Read required sheet columns once and preserve exact source keys.
3. Deduplicate platform IDs/URLs.
4. Resolve provider; read/cache the shared template signature once.
5. Batch research through owner adapters first.
6. Build one merged plan per document.
7. Process independent documents with concurrency 2–3: one dry-run, apply, and compact read-back.
8. Enforce `deliverable_only` for external BRFs.
9. Report verified URLs. Do not reopen POPO when no writeback occurred.

## Isolated known-document edit

If a target document URL and edits are known, and no sheet/research/checkpoint is involved, this skill should not run. Hand off to the designated document skill/provider.

If the known document is the current stage of an existing pipeline checkpoint, resume only `document -> verify`; do not reread the sheet or repeat research.

## Simple deterministic sheet batch

Use the in-memory fast path for one sheet/tab, one destination, one exact key column, one value
policy, and one batch write/read-back—even when several independent research adapters or more than
five entities are involved.

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

Open a fresh sheet tab, scan all row IDs before filtering, freeze selection and exact keys, partition
research by adapter, submit once, and verify exact pairs plus the full eligible-key set.

## Full metric refresh

Use when the user says “刷新 / 更新为最新”.

Contract:

```json
{
  "scope": {"selection": "all_eligible"},
  "writeback": {
    "intent": "refresh_existing",
    "existing_values": "overwrite"
  },
  "verification": {"coverage": true}
}
```

Do not skip an eligible row because its target cell is non-empty. A stale non-empty value is part of the refresh scope. Verify all eligible records, not only rows included in the final write plan.

Before freezing scope, require `scanned_data_rows=total_data_rows`. Matching dates/statuses may appear
in several non-contiguous blocks; never stop after the first block.

## Blank-only fill

Use when the user says “补空 / 填缺失”.

Contract:

```json
{
  "scope": {"selection": "blank_only"},
  "writeback": {
    "intent": "fill_missing",
    "existing_values": "preserve"
  },
  "verification": {"coverage": true}
}
```

Record non-empty eligible targets as policy-allowed `skipped`; do not research them unless requested.

## Multi-tab mixed-platform refresh

Use one manifest with a `sheet.tabs[]` entry for each named tab and its exact key/eligibility/source/target columns. Declare all research sources; the tool plan must include every owning adapter. Freeze/reconcile coverage per tab and in aggregate.

If project/content fields determine templates or destinations, add `routing_rules` and an exactly-one/blocked `routing_policy` before reading the sheet. Scope conditions to `tab_refs`; condition fields must be declared by those tabs, and rule templates must agree with fixed destination templates. Each discovered record must resolve to exactly one rule or become blocked; do not choose a template/destination later from prose.

## Bilibili metric refresh

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

1. Open the POPO URL in a fresh task tab, scan every data row, then freeze scope from live key,
   platform/status, Bilibili URL, and target columns.
2. Copy exact source keys, then deduplicate BV/AV IDs.
3. Run `scripts/bilibili_batch.py --input ...` once.
4. Use browser only for failed/ambiguous IDs.
5. Preserve canonical URL/ID, returned field, and observation time.
6. Re-scan all rows in fresh state, reconcile all eligible keys, batch write, and full-scope read-back.

## Douyin Xingtu metric refresh

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

1. Open the POPO URL in a fresh task tab and scan every row ID.
2. Read structured exact `source_key`, eligibility columns, Douyin item URL/ID, current targets, and
   target columns; freeze only after the full scan.
3. Load `$douyin-xingtu`, self-test once, then run one `published-items`/item batch.
4. Join by unchanged `source_key`; use item/author/star IDs for evidence.
5. Write only `ready`; keep other statuses blocked with reasons.
6. Convert dates/counts to the visible convention, re-scan fresh POPO state, submit one batch, and
   verify exact values plus full eligible-key coverage.

Never click creator cards serially, rebuild Xingtu requests, generate keys from screenshots, or rerun research after a sheet transport failure.

## Master execution sheet to customer platform sheets

Use when one execution/master tab is the source of truth and results must be reflected into customer/platform tabs.

1. Freeze exact master `source_key` values and eligibility from structured master rows.
2. Read each destination's live exact key column/header before planning.
3. Keep `source_key` and normalized `join_key` separate.
4. Produce a reconciliation table: exact match, normalized candidate, missing destination, duplicate, or ambiguous.
5. Auto-write only exact unique matches. A normalized candidate requires a verified identity signal and must retain the destination's own exact display value.
6. For missing destination rows, use the destination's append/upsert rule; never paste into the currently selected cell/row without a fresh structural locator.
7. Write each destination independently and verify full per-destination coverage.

Screenshots/OCR cannot supply the master key list or a writable destination key.

## Template documents and link writeback

Chain: `sheet_context -> web_research -> normalize -> document -> sheet_writeback -> verify`.

Use a manifest when the batch is resumable, exceeds five documents, or has mixed templates/destinations.

Before document creation:

- declare `templates{}` and `destinations[]`;
- route every entity with `template_ref`/`destination_refs`;
- set external documents to `deliverable_only`;
- choose link policy:
  - replace link: `refresh_existing + overwrite`;
  - keep old and new: `merge_existing + merge`;
  - fill only missing link: `fill_missing + preserve`.

If metric fields and document-link fields use different policies in the same job, keep the metric policy as the top-level default and add a `field_policies.document_link` override. Assign metric and link subsets to their sheet destinations with `target_fields`.

Verify a document before including its URL in the sheet batch.

## Unknown document mutation outcome

Use after an apply timeout/connection loss.

1. Preserve destination `running` while the process is live; otherwise mark it `unknown`. Both states require `writer_count=1`, owner, idempotency key, process state, and checkpoint resume.
2. Terminate/wait for the original writer; do not launch another.
3. Read fresh title/content/structure/permission.
4. If intended post-state exists, release the writer and mark verified with `process_state=ended`, `actual_state_checked=true`, and a readback proof.
5. Otherwise compute only the remaining delta and retry once with a new idempotency key/preconditions.
6. Save checkpoint before continuing other destinations.

This prevents duplicate headings, duplicated blocks, and concurrent recovery writes.

## Unknown sheet acknowledgement / cross-session resume

Use when research is complete but write acknowledgement/read-back failed.

1. Read fixed-name `checkpoint.json`; validate compose schema, contract/checkpoint integrity, source snapshot hash, frozen-key/record coverage, destination locks, and verified proofs.
2. Do not rerun verified research or recreate verified documents.
3. Reopen one authenticated destination session and run the live gate.
4. Fetch fresh structured state.
5. Compare every intended exact key/value pair.
6. Mark already-present values verified.
7. Rebuild one plan for unexplained mismatches only and retry once.
8. Verify full frozen coverage and update checkpoint.

## Research to a new table

Chain: `sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.

Decide destination, discovery boundary, exact unique key, fields, duplicate policy, and append/upsert behavior. Create and verify the empty schema before loading records.

## Suggested user prompts

- “用 `$research-sheet-pipeline` 读取这张 POPO 表，只研究这 3 位 B站达人并各生成一份 BRF；不要回填表格。”
- “把执行表所有符合条件的 B站播放量刷新到最新，包括已有值，并核验全量覆盖。”
- “只补目标列为空的行，已有值不要覆盖。”
- “从执行表同步到客户表；名字必须来自结构化单元格，截图不能作为匹配主键。”
- “从 checkpoint 恢复；不要重新研究或重建已验证文档，只核对未知写入结果。”
