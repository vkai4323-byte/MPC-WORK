# Skill handoff

## Scope

This repository contains three composable skills:

- `research-sheet-pipeline` orchestrates spreadsheet context, external research, normalization, optional document work, guarded writeback, and verification.
- `popo-sheet` owns structured POPO workbook access, internal row/column ID mapping, preconditioned
  JSON0 writes, and full-scope readback.
- `douyin-xingtu` owns authenticated, read-only 巨量星图 access for Douyin creator and publication data.

The pipeline decides which rows and fields need research and whether verified fields may enter a
write plan. `popo-sheet` owns POPO transport. `douyin-xingtu` owns Xingtu authentication checks,
endpoint schemas, identity resolution, metric provenance, retry bounds, and the mutation blacklist.

Feishu support is bundled inside `research-sheet-pipeline` through the official `lark-cli` routing
guide, portable provider, and credential-free compatibility adapter. Bilibili support is bundled as
the API-first `bilibili_batch.py`.

## Primary workflow

When a sheet row contains both an expected creator name and a Douyin publication URL or item ID, use:

```powershell
cd skills/douyin-xingtu
python scripts/xingtu_batch.py self-test
python scripts/xingtu_batch.py published-items --input published.json --output verified-results.json
```

`published-items` performs these checks:

1. Resolve the item ID, using one temporary browser tab only when normal short-link resolution fails.
2. Batch-read item details from Xingtu.
3. Search creator candidates serially in the authenticated Xingtu market.
4. Match the item in `last_10_items` or representative `items`.
5. Require item `author_id` to equal candidate `core_user_id`.
6. Add `star_id`, evidence, observation time, source fields, and the exact match method.

Only exact `ready` fields and policy-approved `not_distributed` fields may enter a sheet write plan.
Identity ambiguity blocks the row; a missing or unreadable metric blocks only that field.

If only a publication URL or item ID is available and creator verification is not required, use `items`. If only creator identity is available, use `authors`.

## Metric policy

For publication refreshes, item-detail `stats.watch_cnt` is the current-play source. Creator-search summaries can be cached and may differ. The default behavior is to:

- keep item-detail playback in `metrics.play_count`;
- preserve search-summary values in `observations`;
- emit `cached_summary_play_count_differs_from_item_detail`;
- include an explicit `metric_resolution`.

Use `--strict-conflicts` when every cross-surface difference must block the record as `metric_conflict`.

For multi-platform distribution refreshes:

- `ready`: write the verified field;
- `not_distributed`: write `0` only when absence is proven and the policy says to do so;
- `blocked`: preserve an unreadable linked distribution;
- never suppress ready sibling fields because another platform is blocked.

## Safety and credentials

- The Xingtu skill is read-only.
- Never automate orders, task publication, creator-list mutation, finance, settlement, messaging, audience pushes, permissions, or collaboration changes.
- Authentication is reused from the user's local Kimi WebBridge browser session.
- Never request, print, persist, or commit cookies, tokens, headers, account IDs, credentials, signed URLs, or raw authenticated payloads.
- Do not commit `.deps`, `__pycache__`, `.codex-runs`, WebBridge request files, sheet snapshots, or production research results.

## Validation

Run the deterministic checks from the repository root:

```powershell
python -B -m py_compile skills/popo-sheet/scripts/name_match_tsv.py
python -B -m py_compile skills/douyin-xingtu/scripts/xingtu_batch.py skills/douyin-xingtu/scripts/test_xingtu_batch.py
python -B skills/douyin-xingtu/scripts/test_xingtu_batch.py
python -B skills/douyin-xingtu/scripts/xingtu_batch.py --help
python -B skills/research-sheet-pipeline/scripts/bilibili_batch.py --self-test
python -B skills/research-sheet-pipeline/scripts/compose_chain.py --self-test
```

The test suite covers:

- item membership in representative creator items;
- unrelated creator-item conflicts remaining warnings for name-only queries;
- verified creator identity merging into publication results;
- current item-detail playback winning over cached summaries by default;
- strict conflict mode blocking cross-surface playback differences.

Run the Skill folder validator against all three skill directories in an UTF-8 Python environment
with `PyYAML` available.

## Operational limitations

- Xingtu endpoints are observed internal web endpoints, not a public compatibility guarantee.
- Creator searches use one browser tab and must remain serial.
- A display name alone is not sufficient to select the first fuzzy candidate.
- Signed cover and share URLs are ephemeral evidence, not durable identifiers.
- Existing Codex tasks may retain an older skill catalog; start a new task after installing or updating these skills.

## Continuation guidance

When Xingtu changes:

1. Reproduce the failure with a read-only test.
2. Update `references/xingtu-observed-api.md`.
3. Repair the bundled client without adding write endpoints.
4. Add or update a deterministic regression test.
5. Run the unit suite, Skill validation, and one bounded authenticated smoke test.

Keep the three skills separate. Add new Xingtu capabilities to `douyin-xingtu`, POPO transport
changes to `popo-sheet`, and update only the routing contract in `research-sheet-pipeline`.
