---
name: douyin-xingtu
description: Retrieve authenticated, read-only Douyin creator, published-video, pricing, ranking, content, and task-report data from 巨量星图 through the user's Kimi WebBridge browser session. Use for 星图达人检索、抖音发布链接批量回收、作品播放/互动数据、发布日期核对、作品ID与达人身份交叉校验、达人报价与预期CPM、达人榜单、内容灵感或星图任务报告；also use when research-sheet-pipeline needs verified Xingtu records for sheet writeback. Do not use for ordering, publishing tasks, changing creator lists, finance, settlement, or audience pushes.
---

# Douyin Xingtu

Use the bundled read-only client instead of rebuilding Xingtu requests in chat.

## Run the shortest command

Run from this skill directory:

```powershell
python scripts/xingtu_batch.py self-test
python scripts/xingtu_batch.py items --input items.json --output item-results.json
python scripts/xingtu_batch.py published-items --input published.json --output verified-results.json
python scripts/xingtu_batch.py authors --input authors.json --output author-results.json
python scripts/xingtu_batch.py ranking --output ranking.json
python scripts/xingtu_batch.py task-reports --output task-reports.json
```

The script talks only to the local Kimi WebBridge daemon and reuses the user's authenticated browser state. Never request, print, or persist cookies, tokens, request headers, account IDs, or credentials.

## Choose the identity path

1. If a published-video URL or `item_id` and expected creator name are known, use `published-items`. It cross-checks item `author_id`, candidate `core_user_id`, `star_id`, and membership in `last_10_items` or representative `items`.
2. If only a Douyin video URL or `item_id` is known, use `items`. Treat the item ID as the primary identity.
3. If only a creator name, Douyin ID, or Xingtu ID is known, use `authors`. Do not auto-select the first candidate.
4. Use creator identity in this order: exact `item_id -> author_id/core_user_id -> star_id -> exact display name`.
5. Preserve the user's original `source_key` and display name byte-for-byte. Keep normalized matching values separate.
6. Stop the affected record on `ambiguous`, `identity_conflict`, `metric_conflict`, `not_found`, or `auth_required`.

Read [references/output-contract.md](references/output-contract.md) before composing with another skill or writing to a sheet. Read [references/xingtu-observed-api.md](references/xingtu-observed-api.md) only when extending or repairing the client.

## Interpret metrics safely

- Use `item_publish_time` when a creator-search result provides it.
- Otherwise expose `create_time` as `publish_time` with its exact source field; do not claim it is a separately verified scheduled publish time.
- Prefer item-detail `stats.watch_cnt` for current plays.
- Record `observed_at`, endpoint, and source field for every writable metric.
- For `published-items`, treat item-detail `stats.watch_cnt` as the documented current-play source. Preserve differing cached summaries in `observations`, add `metric_resolution`, and warn explicitly. Add `--strict-conflicts` when any cross-surface difference must block the record as `metric_conflict`.
- Keep conflicts field-scoped. An unrelated item-play conflict is a warning for a pricing-only creator query, not a blocker.
- Treat signed cover/share URLs as ephemeral evidence, not durable IDs.

## Bounded workflow

1. Run `self-test` once.
2. Batch known item IDs in one `items` call.
3. Search unresolved creators serially because one browser tab owns the search state.
4. Retry one failed read once. Do not open extra sessions or reverse-engineer new endpoints during a production run.
5. Return compact JSON and let the caller decide whether to write.

## Failure branches

| Trigger | First recovery | If still unresolved |
|---|---|---|
| HTTP cannot resolve a Douyin short URL | Resolve once in a temporary browser tab, close it, and restore Xingtu. | Return `not_found: unresolved_item_id`. |
| URL-filter creator search produces no network response | Fill the visible creator-search input and submit once. | Return `error: search_response_not_observed`. |
| Item belongs to a different `core_user_id` | Preserve both IDs and stop. | Return `identity_conflict`; never write. |
| Multiple candidates contain the same item ID | Preserve candidates and stop. | Return `ambiguous`; never select by position. |

## Hard safety boundary

Allowed:

- Xingtu market navigation;
- network observation of the search request initiated by that page;
- read-only GET requests executed inside the authenticated Xingtu page;
- the page's search-only POST request;
- local JSON input/output.

Never call or automate:

- 下单、发布任务、创建或修改达人清单；
- 财务、余额、结算、充值、退款；
- 项目状态修改、消息发送、人群推送；
- permission, account, qualification, or collaboration changes.

If a requested operation crosses this boundary, stop and ask for a separate explicit authorization and a purpose-built workflow.
