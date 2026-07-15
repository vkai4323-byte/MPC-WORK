# Module contracts

## Scope

Inputs: user objective, known entities or discovery rule, requested fields, output destinations, side-effect permissions, and completion criteria.

For a fast-path job, keep a compact in-memory contract. A persistent manifest is required only under the complexity rules in `SKILL.md`.

Minimum contract:

```json
{
  "objective": "",
  "entities": [],
  "modules": [],
  "unique_key": "",
  "destinations": [],
  "evidence": "url+source_id+observed_at",
  "completion": [],
  "limits": {"fallbacks_per_stage": 1}
}
```

Default evidence is source URL/ID, returned source fields, and observation time. Add screenshots only on request, for UI-only fields, or to resolve identity/result ambiguity.

## Sheet context and schema

For an existing sheet:

1. Reuse the authenticated task tab and follow the read-only acquisition gate in `SKILL.md`.
2. Read the latest structured state, limited to the named tab and required columns when the protocol permits.
3. Infer the visible key and target columns from headers and nearby completed rows.
4. Record current values and only the formatting conventions needed for the requested output.
5. Normalize visible keys for joins while preserving display text.

For a new sheet:

1. Draft the minimum schema from requested outputs.
2. Include one stable key and evidence/source fields.
3. Add document-link columns only when document creation and writeback are both enabled.
4. Define types, required fields, blank policy, and hyperlink convention.
5. Create only after the schema is internally consistent.

Compact sheet-context output:

```json
{
  "platform": "popo",
  "sheet": "执行",
  "key_column": "达人昵称",
  "required_columns": ["平台", "主页", "目标字段"],
  "target_count": 3,
  "snapshot_version": "latest",
  "errors": []
}
```

Do not return an entire workbook snapshot through the tool channel.

## Web research

Batch API-capable sources before browser work. Use one browser session only for login-dependent sources or unresolved records.

For each entity:

1. Confirm identity using name plus a secondary identifier such as MID, source ID, owner, or canonical URL.
2. Capture only requested fields.
3. Store source URL/ID, observation time, and uncertainty.
4. Preserve partial successes; do not let one failed entity abort a batch.
5. Never silently merge similarly named entities or fabricate missing values.

For Bilibili creator research, a known MID/space URL goes directly to `--creator-input`. A name goes to `--user-search`; only an exact, unambiguous name+MID match proceeds automatically.

## Normalize

Transform evidence into canonical records once. Normalize whitespace, Unicode width, and harmless punctuation for matching, but preserve display names. Convert dates and counts to the destination convention.

Checkpoint summary:

- source and unique-record counts;
- exact, normalized, duplicate, and ambiguous matches;
- unresolved required fields;
- records ready for each side effect.

Stop before affected side effects when duplicates or ambiguity remain.

## Document

Run only when `documents.mode` is `create` or `update`.

Inputs: canonical record, template or target URL, naming rule, common brief, entity-specific direction, release node, permission rule, and required format invariants.

### Provider gate

1. Resolve a provider with `scripts/document_provider.py`; treat manifest validity and runtime readiness as separate gates.
2. Require only the job's needed capabilities: read; copy for template creation; update; exact read-back; and the exact permission operation requested. The bundled adapter supports public-permission read plus `anyone_editable`, not arbitrary sharing policies.
3. If the CLI lacks credentials, check for an authenticated Agent connector with equivalent capabilities. Otherwise instruct the user to configure credentials locally; never request a secret in chat.
4. Do not echo or persist provider paths, argv, `.env` content, or credential values. Do not substitute browser editing.
5. If no provider becomes ready within the preflight budget, stop only `document` and preserve upstream records and evidence.

### Shared preparation

1. Resolve the document/template once.
2. Read the template once and cache a compact structural signature.
3. Check required verbs before mutation: copy, replace, block/style edit, comment/image insertion, permission update, and read-back.
4. If the user asks to preserve exact format, copy the template instead of rebuilding it.

### One transaction per document

1. Build one replacement/edit plan containing common and entity-specific changes.
2. Dry-run the complete plan once.
3. Stop if any target is unmatched, duplicated unexpectedly, crosses styled runs unsafely, or requires an unsupported verb.
4. Apply once when the tool supports a grouped operation. The bundled adapter uses one idempotent batch update for at most 200 affected blocks. If another tool performs sequential block updates, record the pre-edit values and verify every affected block immediately so a partial update is visible and resumable.
5. Apply the permission rule through the designated tool.
6. Perform one compact read-back and append the verified URL to `record.documents`.

Independent documents may run with concurrency 2–3. Operations within one document remain ordered.

### Format signature

Compare the target with the cached template/pre-edit signature:

- block type;
- block nesting and order;
- heading, paragraph, list, and list-level metadata;
- inline style/run boundaries for changed text;
- tables, images, embeds, and other non-text anchors;
- title and sharing state.

Block count is only a coarse checksum and never sufficient by itself.

## Sheet create and writeback

Creation:

1. Create the workbook/sheet on the requested platform.
2. Add headers, types, widths, wrapping, filters, and baseline formats.
3. Verify the empty schema before loading records.

Writeback modes:

- `append`: add records after the current data region.
- `fill`: update requested columns for matched rows.
- `upsert`: update unique matches and append missing keys.

### POPO transport gate

Immediately before a POPO write, confirm in the same editable task tab:

1. WebBridge is connected.
2. The page is online, not reconnecting.
3. The target is editable and not a read-only duplicate.
4. Live sheet title, headers, keys, target columns, and overwrite policy still match the task.

If any check fails, do not fetch sources again, open source links, submit a write, or reuse stale internal IDs.

### Bounded recovery

| Failure | Action | Limit |
|---|---|---|
| Bridge disconnected | Connect once and recheck the same task tab. | Stop writeback if still unavailable. |
| Page offline/reconnecting | Recheck the same tab once after a short wait. | Create at most one fresh task session, then stop if still offline. |
| Online snapshot/op timeout | Retry the same stage after short backoff. | Two attempts for the stage; no growing timeout or extra windows. |
| Fresh session cannot pass gate/read | Preserve canonical records and planned targets. | Stop and report the exact resume action. |
| Acknowledgement uncertain/read-back failed | Fetch fresh state and compare exact key/value pairs. | Retry only unexplained mismatches once. |

Build a current-snapshot plan immediately before the operation:

```json
{
  "key": "",
  "sheetId": "",
  "rowId": "",
  "colId": "",
  "oldValue": "",
  "newValue": "",
  "snapshotVersion": ""
}
```

Discard the plan after any transport failure and rebuild from fresh state. Never reuse row/column IDs, versions, or preconditions.

### Write rules

1. Join on live visible keys, never external row numbers.
2. Preserve non-target fields and styles.
3. Stop on duplicate keys, shifted headers, protected/read-only targets, or non-empty conflicts unless overwrite was requested.
4. Batch current targets using version and old-value preconditions.
5. Read back exact planned pairs.
6. Rebuild and retry only mismatches once; never replay verified rows or completed research.

## Verify

Verify structure first, presentation second:

1. Read only requested output fields from fresh state.
2. Compare exact key/value/link pairs with canonical records.
3. Reconcile created, updated, skipped, partial, and failed counts.
4. For documents, compare the structural signature and sharing state.
5. Screenshot only when visual proof or formatting review matters.
6. Retry only mismatches once after reading actual state.

Completion requires zero unexplained mismatches.

## Failure boundaries

Stop the affected side effect when the schema changed, a target is read-only, duplicate keys exist, a non-empty value conflicts, a document provider is unavailable, a document edit is format-unsafe, or verification is unavailable. Continue independent entities/stages when safe. Preserve the compact canonical checkpoint and report the failed stage, attempts, elapsed budget exception, and exact action needed to resume.
