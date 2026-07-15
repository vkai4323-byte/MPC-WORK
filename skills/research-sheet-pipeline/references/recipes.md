# Composition recipes

## Fast read-only BRF batch

Use for 1–5 entities, one sheet/tab, one template family, and no sheet writeback.

Chain: `sheet_context -> web_research -> normalize -> document -> verify`.

1. Keep the plan in memory; do not create a manifest.
2. Read the required sheet columns once and deduplicate platform IDs/URLs.
3. Resolve the document provider, then read the shared template once and cache its format signature.
4. Batch research through APIs first.
5. Build one merged replacement plan per document.
6. Process independent documents with concurrency 2–3: one dry-run, one apply, one compact read-back each.
7. Report verified document URLs. Do not reopen POPO during final verification because no writeback occurred.

## Known-document follow-up

Use when the target document URL and requested edits are already known.

Chain: `document -> verify`.

Do not read a sheet, run creator research, create a manifest, or re-resolve an upstream template unless the edit explicitly depends on it. Resolve the provider, read the target once, capability-check the requested verbs, dry-run one combined edit plan, apply once, then compare required facts and the pre-edit structural signature.

## Research-only bulk fill

Use when a sheet exists and documents are unnecessary.

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

Read the sheet first to obtain authoritative key order and target columns. Research in batches, normalize once, produce a duplicate/missing report, write one batch, and read back only target key/value pairs.

## Template documents and link writeback

Use for BRFs, outreach briefs, evaluations, or contracts that must be linked back to a sheet.

Chain: `sheet_context -> web_research -> normalize -> document -> sheet_writeback -> verify`.

This is not the read-only fast path. Use a manifest when the batch is resumable, spans more than five documents, or has mixed templates/destinations. Verify each document before including its URL in the one sheet writeback batch.

## Fast Bilibili metric refresh

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

1. Read only live key, platform/status, Bilibili URL, and target metric columns.
2. Filter eligible rows and deduplicate BV/AV IDs.
3. Run `scripts/bilibili_batch.py --input ...` once.
4. Use Kimi WebBridge only for failed or ambiguous IDs.
5. Normalize counts and retain canonical URL/ID plus observation time.
6. Refresh POPO once, batch write, and perform one compact read-back.

## Bilibili creator BRF research

Use for known creator MIDs/space URLs or creator-name discovery.

1. If MID/space URL is known, batch `--creator-input ... --recent N`.
2. If only the name is known, run `--user-search`; confirm normalized name and MID before lookup.
3. Treat fewer than `N` returned recent uploads as `partial`, not complete.
4. Send only unresolved/ambiguous creators to Kimi WebBridge.
5. Keep evidence as MID, canonical space URL, returned fields, and observation time.

## From research to a new table

Chain: `sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.

Decide destination, discovery boundary, unique key, required fields, duplicate policy, and append/upsert behavior. Create and verify the empty schema before loading records.

## Partial refresh

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

Constrain work to named keys and target columns. Preserve current values when a fresh source is unavailable unless the user explicitly asks to clear them.

## Suggested user prompts

- “用 `$research-sheet-pipeline` 读取这张 POPO 表，只研究这 3 位 B站达人并各生成一份 BRF；不要回填表格。”
- “用 `$research-sheet-pipeline` 按表里的 B站 space URL 批量获取最近 5 条投稿，再把结果按达人昵称回填。”
- “用 `$research-sheet-pipeline` 只研究并输出规范化数据和证据，不写表、不做文档。”
- “这份飞书文档地址已经确定，只修改指定两段并验证格式；不要重新读取 POPO 或做外部研究。”
