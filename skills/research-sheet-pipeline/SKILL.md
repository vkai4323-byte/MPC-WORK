---
name: research-sheet-pipeline
description: Compose fast, evidence-backed workflows that read a sheet, research external creator/content data, and optionally write results or document links back. Use for POPO/online-sheet metric refreshes, Bilibili or Douyin/Xingtu creator data, BRF batches sourced from sheets, name-matched fills, multi-platform research, and resumable sheet/document pipelines. Do not use for an isolated edit to one already-known document when no sheet, research, or pipeline state is involved.
---

# Research Sheet Pipeline

Default to the fixed fast path. Adapter count does not make a job complex.

## Route once

| Data/action | Owner |
|---|---|
| POPO read/write | `$popo-sheet` in one `$kimi-webbridge` task session |
| Any Feishu online document work | official `lark-cli` via `scripts/document_provider.py` |
| Bilibili video/creator data | bundled `scripts/bilibili_batch.py` |
| Douyin/Xingtu data | `$douyin-xingtu` |
| Documents or unknown providers | complex path |

Any `feishu.cn`, `larksuite.com`, or compatible `/wiki/`, `/docx/`, `/sheets/`,
`/base/`, `/bitable/`, `/slides/`, `/drive/` resource must first read
`references/feishu-cli.md`. Do not use a browser bridge to read or mutate Feishu
online documents.

Load selected companion skills. Do not load pipeline references for the fixed path.

Load `references/tool-routing.md` only for an unknown provider; `references/recipes.md` only for a
named complex recipe; and `references/module-contracts.md` only for complex/resumable work,
document side effects, or an unknown mutation result. For Xingtu writeback, also load its
`references/output-contract.md`.

## Fixed fast path

Use this path for one input tab and one sheet destination when key, eligibility, targets, and write
policy are deterministic; there is no document creation, new sheet, cross-session resume, or unknown
write result.

Do not create a manifest, checkpoint, screenshot, or debug payload.

### S0 — fresh sheet and full scope

1. Start one Kimi WebBridge task session.
2. Open the supplied POPO URL with `newTab:true`; never mutate an old `只读`/`已离线` tab.
3. Let `$popo-sheet` fetch one structured snapshot and enumerate every live data-row ID.
4. Only after the complete enumeration, apply the structured eligibility predicate.
5. Freeze each match as exact `row_id`, byte-for-byte `source_key`, platform, source URL/ID, targets,
   and write policy.

Required gate:

```text
scanned_row_ids = live_data_row_ids
scanned_data_rows = total_data_rows
filter_after_full_scan = true
```

Matches may be non-contiguous. Stop on partial scan, blank/duplicate exact key, duplicate row ID,
missing header, or ambiguous policy.

Policy vocabulary:

- “刷新/更新为最新” → all eligible, overwrite named fields.
- “补空/填缺失” → eligible blanks only, preserve non-empty fields.
- “只处理这些名字” → named subset with the stated value policy.

### R — one batch per platform

Partition frozen records by platform and run non-empty adapters in parallel. Each adapter gets one
self-test and one batch; deduplicate canonical item IDs inside that batch.

- Bilibili video URL/BV/AV: `scripts/bilibili_batch.py --input`.
- Bilibili creator MID/space URL: `--creator-input`; name discovery: `--user-search`.
- Douyin/Xingtu published item: `$douyin-xingtu published-items`.
- Other authenticated source: one bounded `$kimi-webbridge` fallback.

Normalize only into a comparison-only `join_key`. Merge results only by exact `source_key`. Keep row
identity status separately from per-target field status. For every target field retain
`ready`, `not_distributed`, or `blocked`, plus source, `observed_at`, and evidence/error. Never invent
identity or discard one platform/field because another failed.

Before writeback, normalize adapter fields into the declared targets:

| Canonical target | Bilibili result | Xingtu result |
|---|---|---|
| play count | `play_count` | `metrics.play_count` |
| likes | `like_count` | `metrics.like_count` |
| comments | `comment_count` | `metrics.comment_count` |
| shares | `share_count` | `metrics.share_count` |
| favorites | `favorite_count` | `metrics.favorite_count` |
| publish time, only if requested | `pubdate` | `metrics.publish_time` |

Map every adapter field explicitly by semantic name; never positionally shift values when a field is
missing. A missing/ambiguous field blocks only that target field. Write other exact ready fields in
the same row. Block the whole row only for duplicate/ambiguous identity or an explicitly declared
row-atomic contract.

For distribution-platform refreshes:

- no distribution link + policy says “没有的填0” → `not_distributed`, value `0`;
- distribution link exists but cannot be read → `blocked`, preserve the current cell;
- another platform in that row is `ready` → still write and verify it.

Choose an authoritative source before comparing values. “Latest” means the newest observation from
that source (or the user's declared source hierarchy), not the largest number.

Do not rerun verified research because sheet transport later fails.

### S1 — one conditional write

On the fresh task tab, `$popo-sheet` fetches a new snapshot, repeats the complete scan, and requires:

```text
live_eligible_keys - frozen_eligible_keys = empty
frozen_eligible_keys - live_eligible_keys = empty
```

It then resolves current IDs/version and writes all ready fields plus policy-approved
`not_distributed` fields in one preconditioned batch. Blocked sibling fields remain unchanged.
There is exactly one writer. On unknown acknowledgement, read actual state before one delta retry.
If the bound tab has gone offline, `$popo-sheet` opens one fresh replacement instead of refreshing
the stale page.

### S2 — full-scope verification

From another fresh snapshot, repeat the full scan and compare every requested value for every frozen
ready key.

Completion requires:

```text
scanned_row_ids = live_data_row_ids
eligible_keys_after = frozen_eligible_keys
eligible_fields = ready_fields + not_distributed_fields + blocked_fields
writable_fields = changed_fields + unchanged_fields
verified_ready_fields = ready_fields + not_distributed_fields
unexplained_mismatches = 0
```

The denominator comes from the live post-scan scope, never from the write-plan length. A zero-mismatch
subset is not completion.

## Complex path

Use schema `3.0` manifest/checkpoint only for multiple input tabs/destinations, mixed document and
sheet side effects, dynamic routing, sheet creation, more than five documents, cross-session resume,
or an unknown mutation outcome.

Then load `assets/job-template.json`, `scripts/compose_chain.py`, and only the matching complex
references. Keep normal artifacts to `checkpoint.json`, final change plan, and verification summary.

## Hard stops

- Never filter after a fixed upper bound, `slice`, viewport sample, early `break`, or first matching
  block.
- Never use screenshots, OCR, row numbers, or normalized names as writable keys.
- Never reuse an old offline/read-only POPO tab for mutation.
- Never reconstruct owner-specific POPO/Xingtu transport in this pipeline.
- Never suppress ready fields because another platform/field in the same row is blocked.
- Never treat an unreadable linked distribution as not distributed or write `0` for it.
- Never start a second writer while the first result is unknown.
- Never report completion from acknowledgement, screenshot, or subset verification.
- Never persist credentials, disposable tokens, raw workbook snapshots, or unnecessary debug files.

## Report

Report total/scanned rows, frozen/live eligible count, platform batch counts, field-level
ready/not-distributed/blocked and changed/unchanged counts, rows/cells written, full readback result,
and unresolved exact row + platform/column keys.
