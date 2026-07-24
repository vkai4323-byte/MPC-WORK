# Output contract

Every item-oriented result uses this shape:

```json
{
  "source_key": "表格中的原始达人名",
  "query": {
    "display_name": "",
    "item_url": "",
    "item_id": ""
  },
  "identity": {
    "author_id": "",
    "core_user_id": "",
    "star_id": "",
    "match_method": "item_id",
    "confidence": "exact"
  },
  "metrics": {
    "publish_time": "",
    "play_count": null,
    "like_count": null,
    "comment_count": null,
    "share_count": null,
    "favorite_count": null,
    "interaction_rate": null,
    "duration_seconds": null
  },
  "content": {
    "title": "",
    "topics": [],
    "canonical_url": ""
  },
  "evidence": [
    {
      "endpoint": "",
      "source_field": "",
      "observed_at": ""
    }
  ],
  "observations": [],
  "status": "ready",
  "errors": []
}
```

## Statuses

| Status | Meaning | Caller action |
|---|---|---|
| `ready` | Exact identity and requested fields are available. | May enter a write plan. |
| `partial` | Exact identity exists but at least one requested field is unavailable. | Write only explicitly allowed fields. |
| `ambiguous` | Multiple creator candidates remain. | Do not auto-select or write. |
| `identity_conflict` | Item `author_id` and selected creator `core_user_id` disagree. | Preserve both identities and do not write. |
| `metric_conflict` | Multiple Xingtu surfaces disagree on a writable metric. | Preserve observations and stop the record. |
| `not_found` | No item or creator was returned. | Leave destination blank. |
| `auth_required` | The Xingtu authenticated page or API is unavailable. | Stop the Xingtu stage. |
| `error` | Transport or schema failure. | Retry the read once, then stop. |

## Input forms

`items` accepts a JSON array of strings or objects:

```json
[
  "https://www.douyin.com/video/7665305601949492403",
  {
    "source_key": "彭昱畅",
    "display_name": "彭昱畅",
    "item_url": "https://www.douyin.com/video/7665305601949492403"
  },
  {
    "source_key": "another row",
    "item_id": "7665305601949492403"
  }
]
```

`authors` accepts names or keyed objects:

```json
[
  "彭昱畅",
  {"source_key": "执行表原名", "display_name": "彭昱畅"}
]
```

Keep `source_key` unchanged across research, normalization, and sheet writeback.

`published-items` accepts keyed records that pair the sheet identity with the publication:

```json
[
  {
    "source_key": "执行表第7行",
    "display_name": "韩堡包",
    "item_url": "https://v.douyin.com/example/"
  },
  {
    "source_key": "执行表第8行",
    "display_name": "昵称可能已变化",
    "item_id": "7665370585678447348"
  }
]
```

The command may select a candidate by exact item membership even when the current display name differs. A writable `ready` result requires item `author_id == core_user_id`; it also includes `star_id` and the exact `match_method`.

By default, `published-items` keeps item-detail `stats.watch_cnt` as `metrics.play_count` and records older creator-search summaries under `observations`. When they differ, `metric_resolution` states the selected surface and `warnings` records the discrepancy. Use `--strict-conflicts` to convert that discrepancy into `status: metric_conflict`.
