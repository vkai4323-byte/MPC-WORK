# Composition recipes

## Contents

- From research to a new table
- Research-only bulk fill
- Template documents and link writeback
- Partial refresh
- Suggested user prompts

## From research to a new table

Use when no workbook exists.

Chain: `scope -> sheet_schema -> web_research -> normalize -> sheet_create -> sheet_writeback -> verify`.

Example: research 100 creators, design columns for nickname, platform ID, followers, homepage, category, evidence screenshot, then create a POPO workbook and load all records.

Key decisions: destination platform, discovery boundary, unique key, required fields, duplicate policy, and append/upsert behavior.

## Research-only bulk fill

Use when the sheet exists and documents are unnecessary.

Chain: `scope -> sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

Read the sheet first to obtain the authoritative name order and target columns. Research in batches, normalize once, produce a duplicate/missing report, and write contiguous blocks. Keep unmatched rows unchanged.

## Template documents and link writeback

Use for BRFs, outreach briefs, evaluations, or contracts derived from a shared template.

Chain: `scope -> sheet_context -> web_research -> normalize -> document -> sheet_writeback -> verify`.

Create each document from the chosen template, customize entity-specific sections, set requested permissions, and verify before attaching its URL to the record. Write only verified links back to the sheet.

## Partial refresh

Use when only some fields or entities are stale.

Chain: `scope -> sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

Constrain research and writes to named keys and target columns. Preserve current values when a fresh source is unavailable unless the user explicitly wants them cleared.

## Fast Bilibili metric refresh

Chain: `sheet_context -> web_research -> normalize -> sheet_writeback -> verify`.

1. Read only the live key, platform/status, Bilibili URL, and target metric columns.
2. Filter published rows with valid Bilibili links and deduplicate BV/AV IDs.
3. Run `scripts/bilibili_batch.py` once; use Kimi WebBridge only for failed or ambiguous IDs.
4. Normalize numeric counts and preserve API URL/ID plus observation time as evidence.
5. Refresh the POPO snapshot once, resolve keys/IDs, batch write, and perform one fresh read-back.

Do not create a job manifest or one screenshot per video for this recipe unless the user explicitly requires an audit package.

## Suggested user prompts

- “用 `$research-sheet-pipeline`，没有现成表格。查找这批达人主页信息，按昵称、平台 ID、粉丝数、主页、类型和截图新建 POPO 表格。”
- “用 `$research-sheet-pipeline`，读取这张表，只查粉丝数和主页并按达人昵称批量回填，不需要做文档。”
- “用 `$research-sheet-pipeline`，读取执行表，研究三位达人，复制允崽 BRF 模板制作飞书文档，设所有人可编辑，再把链接回填。”
- “用 `$research-sheet-pipeline`，只研究并输出规范化数据和证据，不写表、不做文档。”
