# Observed Xingtu read-only surfaces

Observed against the authenticated advertiser market on 2026-07-24. These are internal web endpoints, not a public compatibility guarantee. Repair the bundled client when schemas change; do not improvise write endpoints.

## Stable entry

- Use `https://www.xingtu.cn/ad/creator/market`.
- `/ad/creator/index` may redirect to the public landing page.

## Creator search

- The market page submits `POST /gw/api/gsearch/search_for_author_square`.
- `GET /gw/api/gsearch/search_intent_authors` reports whether a query is semantically consistent.
- Search responses may include `star_id`, `core_user_id`, `nick_name`, followers, labels, prices, expected plays, CPM/CPE, interaction/completion metrics, indices, representative items, and `last_10_items`.
- A name query can return unrelated or imitation accounts. Never select by result position.
- For publication verification, search both parsed `last_10_items` and top-level representative `items`. An exact target item in one candidate is stronger evidence than display-name equality.
- Cross-check item-detail `author_id` against candidate `core_user_id` before exposing the record as writable.

## Item detail

`GET /gw/api/data_sp/external_multi_get_item`

Observed fields include item/author ID, create time, duration, title, topics, cover, share URL, and:

- `stats.watch_cnt`
- `stats.like_cnt`
- `stats.comment_cnt`
- `stats.share_cnt`
- `stats.favorite_cnt`
- `stats.interact_rate`

Search summaries, `last_10_items`, and item detail may disagree because of cache or refresh timing. Emit a conflict rather than silently choosing across surfaces.

Some item endpoints serialize 19-digit IDs as JSON numbers. Never parse and re-stringify those bodies in browser JavaScript because IEEE-754 rounding corrupts the ID. Return raw response text to Python and parse it there.

Douyin short links may not resolve through a plain HTTP client. Use at most one temporary WebBridge tab as a fallback, close it immediately, and restore the Xingtu market tab.

## Discovery

- `GET /gw/api/fe_common_service/author_options/market_fields`
- `GET /gw/api/gsearch/get_search_field_options`
- `GET /gw/api/gsearch/get_ranking_list_catalog`
- `GET /gw/api/gsearch/get_ranking_list_data`
- `POST /gw/api/gsearch/search_for_content_square`
- `GET /gw/api/gsearch/content_square_sync_date`

These support creator filters, taxonomies, rankings, and content-inspiration research.

## Task reporting

`GET /gw/api/data_sp/project_task_report_info`

The page exposes task information, release date, project, reach, shares, likes, and an export action. Treat task reports as account-private output and return only fields requested by the user.

## Mutation blacklist

Do not call endpoints or UI actions related to orders, task creation, author-list mutation, settlement, balance, qualification, collaboration accounts, messaging, or audience pushes.
