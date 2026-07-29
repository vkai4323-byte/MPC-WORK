# Module contracts

## Contents

- Scope
- Persistent checkpoint
- Typed destinations
- Multi-tab inputs and routing rules
- Sheet context and schema
- Web research
- Normalize
- Document
- Sheet create and writeback
- Verify
- Failure boundaries

## Scope

Inputs: objective, eligibility rule, exact source entities or discovery rule, requested fields, existing-value policy, output destinations, side-effect permissions, and completion criteria.

This reference is only for the complexity/resume conditions in `SKILL.md`. A fixed fast-path job must
not load, instantiate, or checkpoint this schema.

Minimum mutation contract:

```json
{
  "objective": "",
  "scope": {
    "selection": "all_eligible",
    "eligibility_source": "sheet columns or explicit user rule",
    "source_key_policy": "exact_structured"
  },
  "sheet": {
    "tabs": [
      {
        "name": "B站",
        "key": "达人昵称",
        "eligibility_columns": ["状态"],
        "source_columns": ["视频链接"],
        "target_columns": ["播放量"]
      }
    ]
  },
  "entities": [],
  "routing_policy": {},
  "routing_rules": [],
  "destinations": [],
  "writeback": {
    "intent": "refresh_existing",
    "existing_values": "overwrite",
    "target_columns": ["play_count", "document_link"],
    "field_policies": {
      "document_link": {
        "intent": "merge_existing",
        "existing_values": "merge"
      }
    }
  },
  "verification": {"coverage": true},
  "execution": {
    "artifact_mode": "minimal",
    "resume": "checkpoint",
    "checkpoint_file": "checkpoint.json"
  }
}
```

### Selection and value semantics

| User intent | `writeback.intent` | `selection` | `existing_values` |
|---|---|---|---|
| Refresh/update the named fields to latest | `refresh_existing` | `all_eligible` | `overwrite` |
| Fill blanks/missing values | `fill_missing` | `blank_only` | `preserve` |
| Only named entities | Applicable intent | `named` | Explicitly choose one value policy |
| Preserve both old and new links/values | `merge_existing` | Applicable selection | `merge` |

Ask before mutation when the wording does not determine one row/value policy.

The eligibility source is authoritative. A downstream document, screenshot, inferred content type, or another table may reveal a conflict but cannot silently add/remove an eligible key.

🔴 **Scope checkpoint:** scan every data-row ID before applying eligibility, then freeze exact eligible
keys, target fields, destinations, and the coverage equation. Record
`scanned_data_rows=total_data_rows` and matched row indexes. Every later omission needs an explicit
`skipped` or `blocked` reason.

## Persistent checkpoint

The checkpoint is a compact execution ledger, not a debug dump. Hashes below are shape examples; the helper always recomputes them:

```json
{
  "schema_version": "3.0",
  "job_id": "job-20260727-001",
  "checkpoint_status": "resuming",
  "contract_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "checkpoint_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "skill_snapshot": {
    "commit": "working-tree",
    "hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
  },
  "adapter_versions": {"popo-sheet": "3.0"},
  "source_snapshot": {
    "ref": "input/sheet-snapshot.json",
    "hash": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
    "observed_at": "2026-07-27T12:00:00+08:00"
  },
  "scope": {},
  "stage_state": {
    "sheet_context": "verified",
    "web_research": "verified",
    "document": "pending",
    "sheet_writeback": "pending",
    "verify": "pending"
  },
  "attempts": {
    "sheet_context": 1,
    "web_research": 1,
    "document": 0,
    "sheet_writeback": 0
  },
  "frozen_source_keys": ["exact creator key"],
  "records": [
    {
      "source_key": "exact creator key",
      "join_key": "exact creator key",
      "source_ref": {"sheet": "execution", "tab": "target", "key_column": "creator"},
      "eligibility": {"status": "eligible", "reason": ""},
      "fields": {},
      "evidence": [],
      "stage_state": {
        "sheet_context": "verified",
        "web_research": "verified",
        "document": "pending",
        "sheet_writeback": "pending",
        "verify": "pending"
      },
      "destinations": {"customer-sheet": {"status": "unknown"}},
      "verified_outputs": {},
      "errors": []
    }
  ],
  "destination_state": {
    "customer-sheet": {
      "status": "unknown",
      "writer_count": 1,
      "writer_owner": "sheet-writer-1",
      "idempotency_key": "write-001",
      "process_state": "unknown",
      "actual_state_checked": false
    }
  },
  "verified_outputs": {},
  "unexplained_mismatches": [],
  "resume_from": "document",
  "updated_at": "2026-07-27T12:01:00+08:00"
}
```

Allowed stage states: `pending`, `running`, `ready`, `verified`, `blocked`, `unknown`.

`checkpoint_status=new` initializes the complete shape with empty progress, frozen-key, record, destination, and proof fields. `checkpoint_status=resuming` is valid only under schema 3.0 with non-empty contract/checkpoint hashes, skill hash, adapter versions, exact source snapshot identity/time, stage attempts, exact `frozen_source_keys`, full canonical records covering those keys exactly once, verified proofs, and a known first-unverified `resume_from` module. When explicit entities are declared, their exact keys and the frozen keys must be identical.

A `running` or `unknown` destination is valid only with `execution.resume=checkpoint`, one logical writer lock, a writer owner, an idempotency key, and explicit process state. The plan is not runnable while either remains unresolved. A `verified` destination requires no writer lock, `process_state=ended`, `actual_state_checked=true`, and matching global/per-record `verified_outputs` entries containing non-empty `readback_hash` and `observed_at`. A globally verified mutation/verify stage requires the same stage-level and per-record proof, and `resume_from` cannot point to it.

Write atomically to the fixed name `checkpoint.json` with `scripts/checkpoint_artifacts.py write-checkpoint` after every verified stage and before returning from a blocked/unknown stage. The helper fails closed on non-v3/incomplete manifests, protected exact keys, or compose validation errors; redacts structured fields, URL queries, emails, credential headers/assignments, and private keys; calculates hashes; then uses a same-directory temporary file plus atomic replace. `verify-dir` scans allowlisted and debug text/JSON content as well as names, caps, and retention. Do not hand-write, partially overwrite, or persist an unredacted checkpoint.

1. read and validate the checkpoint first;
2. recompute and compare its contract/checkpoint hashes, then compare the referenced source snapshot hash with fresh source acquisition;
3. treat verified work as immutable;
4. revalidate only live destination state;
5. continue from the first unverified/unknown stage.

If the contract changed, create a new job/checkpoint instead of silently mutating history.

## Typed destinations

Every sheet destination in a multi-destination manifest declares platform, live sheet name, semantic-to-column mapping, and optional accepted content types:

```json
{
  "id": "customer-bilibili",
  "type": "sheet",
  "platform": "popo",
  "sheet_name": "B站",
  "target_fields": ["main_link", "publish_date", "play_count"],
  "field_map": {
    "main_link": "发布链接",
    "distribution_link": "分发链接",
    "publish_date": "发布日期",
    "play_count": "播放量"
  },
  "accepted_content_types": ["video"]
}
```

Semantic fields within one destination must map to distinct columns. `target_fields` assigns the subset handled by that destination; every job target must be assigned at least once and match a semantic key or live-column value in its destination. When `accepted_content_types` is present, every explicitly routed entity must declare `content_type`, and it must be accepted. Re-resolve live headers/column IDs immediately before writing and compare them with this mapping.

## Multi-tab inputs and routing rules

Declare each input tab independently:

```json
{
  "name": "抖音",
  "key": "达人昵称",
  "eligibility_columns": ["状态"],
  "source_columns": ["作品链接", "项目类型", "内容类型"],
  "target_columns": ["播放量", "BRF链接"]
}
```

Tab names must be unique and every tab needs its own exact key. A writeback job may use top-level `sheet.key` or keys on every declared tab.

For entities discovered after the manifest is created, route by declared source fields:

```json
{
  "id": "project-a-video",
  "when": {"field": "项目类型", "equals": "A"},
  "tab_refs": ["B站", "抖音"],
  "template_ref": "template-a",
  "destination_refs": ["brief-a", "video-review"],
  "content_types": ["video"]
}
```

Each rule has a unique ID, exactly one `equals` or `in` condition, valid template/destination refs, and content types compatible with all routed destinations. A condition field must be declared by at least one input tab, or by every tab listed in `tab_refs`. A rule template must equal the fixed template on every routed document destination. `routing_policy` requires exactly one match and marks zero/multiple matches blocked.

Multiple templates/destinations with no explicit entities require routing rules. For explicit entities, use object records with `template_ref`, `destination_refs`, and required `content_type`; bare strings are invalid when destinations exist.

## Sheet context and schema

For an existing sheet:

1. Open the supplied URL once in a fresh authenticated task tab. Do not borrow an earlier tab for mutation.
2. If an old tab is offline/read-only, leave it and use one new task tab with the original URL.
3. Read the latest structured state and enumerate every data-row ID in the named tab.
4. Determine eligibility from authoritative columns/user rules only after the full enumeration.
5. Copy exact visible keys from structured cells/export and record current target values.
6. Compact the result after filtering; never truncate, sample, or stop at the first contiguous block.

Compact output:

```json
{
  "platform": "popo",
  "sheet": "执行",
  "key_column": "达人昵称",
  "required_columns": ["平台", "主页", "目标字段"],
  "total_data_rows": 136,
  "scanned_data_rows": 136,
  "eligible_source_keys": ["与山0v0"],
  "target_count": 1,
  "matched_row_indexes": [42],
  "snapshot_version": "latest",
  "errors": []
}
```

Do not return an entire workbook snapshot through the tool channel. Do not transcribe keys from screenshots/OCR when structured data is required for writeback.

For a new sheet:

1. Draft the minimum schema from requested outputs.
2. Include one stable exact key and evidence/source fields.
3. Add document-link columns only when document creation and writeback are both enabled.
4. Define types, required fields, blank policy, hyperlink convention, and append/upsert behavior.
5. Verify the empty schema before loading records.

## Web research

Use the owner adapter for API/authenticated sources; use one browser session only for login-dependent or unresolved records.

For each entity:

1. Keep the structured string `source_key` unchanged; reject non-string values and never trim, coerce, case-fold, or Unicode-normalize the stored key.
2. Confirm identity with source ID/canonical URL plus name or owner.
3. Capture only requested fields.
4. Store URL/ID, returned source field, observation time, and uncertainty.
5. Preserve partial success; one failed entity must not abort the batch.
6. Never merge similarly named entities or fabricate values.

For Bilibili, a known MID/space URL goes to `--creator-input`; a name goes to `--user-search`, and only an exact unambiguous name+MID match proceeds.

For authenticated Douyin/Xingtu:

1. Send known item URLs/IDs to `$douyin-xingtu` in one batch.
2. Match `item_id -> author_id/core_user_id -> star_id -> exact display name`.
3. Preserve endpoint, source field, `observed_at`, and alternative observations.
4. Treat `ambiguous`, `identity_conflict`, `metric_conflict`, `not_found`, and `auth_required` as non-writable.
5. Use the returned publish-time field exactly; do not relabel another timestamp.

## Normalize

Each record has:

- `source_key`: exact structured key used for writes;
- `join_key`: comparison-only normalized value;
- `source_ref`: sheet/tab/key-column or explicit-user source;
- `eligibility`: status and reason;
- normalized `fields`;
- evidence;
- per-stage and per-destination state.

Normalize whitespace, Unicode width, and harmless punctuation only into `join_key`. Never write the normalized value back over the exact key.

Checkpoint summary:

- eligible, source, and unique-record counts;
- exact, normalized, duplicate, and ambiguous matches;
- unresolved required fields;
- row identity status plus target fields ready/not-distributed/skipped/blocked per side effect.

Stop affected side effects on duplicate exact keys or unresolved identity.

## Document

Run only when `documents.mode` is `create` or `update`.

Inputs: canonical record, template/target, destination, naming rule, common brief, entity-specific content, permission rule, audience/content policy, and format invariants.

### Provider gate

1. Resolve a provider with `scripts/document_provider.py`; manifest validity and runtime readiness are separate gates.
2. Require only exact capabilities: read, copy when needed, update, structural read-back, and requested permission operation.
3. If the CLI lacks credentials, check an authenticated Agent connector with equivalent capability. Otherwise stop `document`; never request secrets in chat.
4. Do not echo/persist provider paths, argv, `.env` content, or credential values. Do not substitute browser editing.
5. If unavailable within budget, preserve upstream records and resume from `document`.

### Shared preparation

1. Resolve each template once.
2. Cache a compact structural signature.
3. Capability-check copy, replace, block/style edit, comment/image insertion, permission update, and read-back.
4. Copy the template when exact format preservation is requested.
5. For `audience=external`, require `content_policy=deliverable_only`.

### One transaction and one writer

1. Acquire exclusive ownership of the destination.
2. Build one merged content/edit plan.
3. Dry-run once.
4. Stop on unmatched/duplicated targets, unsafe styled runs, unsupported verbs, or audience-policy violations.
5. Apply once with an idempotency key when supported.
6. Apply the permission rule.
7. Read back content, structure, permission, and URL.
8. Mark verified only after exact read-back.

If apply times out or acknowledgement is unknown:

1. mark destination `unknown`;
2. keep its writer lock;
3. ensure the original writer/process has ended;
4. fetch fresh actual state;
5. mark verified if the intended post-state exists;
6. otherwise compute the remaining delta and retry once.

Never start a second recovery writer while the original outcome/process is unresolved.

Independent destinations may run with concurrency 2–3. Operations within one destination remain ordered.

### Clean deliverable and structure

For an external deliverable, reject internal rationale, task instructions, troubleshooting notes, placeholders, prompt echoes, and implementation commentary. Keep them only in the change plan/checkpoint.

Compare:

- block type, nesting, and order;
- heading/paragraph/list metadata;
- inline style/run boundaries;
- tables, images, embeds, and other non-text anchors;
- title, required facts, and sharing state.

Block count is only a coarse checksum.

## Sheet create and writeback

Modes:

- `append`: add records after the current data region;
- `fill`: update requested columns for matched exact keys;
- `upsert`: update unique matches and append missing exact keys.

Intent must be explicit and compatible with the primitive:

- `refresh_existing`: `fill` + `all_eligible|named` + `overwrite`;
- `fill_missing`: `fill` + `blank_only|named` + `preserve`;
- `merge_existing`: `fill` + `merge`;
- `append`: `append`;
- `upsert`: `upsert`;
- `audit_only`: only when writeback mode is `none`.

The top-level intent/value policy is the default. When named targets differ, use `field_policies` keyed by an entry in `target_columns`. Each override repeats `intent` and `existing_values` and may optionally narrow `selection`; it must satisfy the same compatibility rules. Example: `play_count` inherits refresh/overwrite while `document_link` overrides to merge/merge.

Existing-value policies:

- `preserve`: skip non-empty targets and record the reason;
- `overwrite`: replace current target values for every eligible record;
- `merge`: preserve old and new values using the visible destination convention.

### POPO transport gate

Immediately before a POPO write, confirm in the same editable task tab:

1. WebBridge is connected.
2. Page is online, not reconnecting.
3. Target is editable and not a read-only duplicate.
4. The tab was opened fresh for this task rather than borrowed from an earlier run.
5. A full-row scan reproduces the frozen exact eligible-key set.
6. Live sheet title, headers, target columns, selection, and value policy match the frozen contract.

Use `$popo-sheet` for structured read/write semantics. Do not reconstruct ShareDB/WebSocket operations in this pipeline.

### Shared attempt ledger

| Failure | Action | Total limit for the stage |
|---|---|---|
| Bridge disconnected | Connect once and recheck same tab. | Stop if still unavailable. |
| Old page offline/read-only | Open the original URL once in a fresh task tab. | Stop if the fresh tab fails. |
| Snapshot/op timeout | Use `$popo-sheet`'s staged 45s default, inspect `open/begin/init/sent/recv`, then retry the same adapter stage after short backoff with fresh IDs/version. | Two attempts total; no ad hoc shorter/growing timeout. |
| Fresh session fails gate/read | Preserve checkpoint and planned targets. | Stop and report resume action. |
| Acknowledgement unknown | Lock destination and read actual state. | Retry unexplained delta once. |

Switching tools, tabs, snapshots, screenshots, or turns does not reset this ledger.

Build a current-snapshot plan:

```json
{
  "source_key": "",
  "sheetId": "",
  "rowId": "",
  "colId": "",
  "oldValue": "",
  "newValue": "",
  "snapshotVersion": ""
}
```

Discard internal IDs/preconditions after any transport failure and rebuild from fresh state.

### Write rules

1. Join with exact live `source_key`, never row number, OCR text, or `join_key`.
2. Resolve each semantic field through the destination `field_map`; stop if two semantics map to one column or the live header/schema changed.
3. Reject records whose `content_type` is not accepted by the destination.
4. Reconcile all frozen eligible row keys and target-field statuses before submit.
5. Preserve non-target and blocked sibling fields/styles.
6. Stop on duplicates, shifted headers, protected targets, or policy conflicts.
7. Submit one batch containing every ready/policy-approved `not_distributed` field with
   version/old-value preconditions.
8. Read back exact intended pairs.
9. Keep verified rows out of retries.

## Verify

Verification is against frozen scope, not only the produced change plan:

1. Require `verification.readback=true` for every sheet, document, or create side effect; the flag cannot be disabled.
2. Enumerate every data row again, then reapply the frozen eligibility predicate.
3. Require `scanned_data_rows=total_data_rows` and exact eligible-key equality with the frozen set.
4. Re-read requested output fields and compare exact row/field/value/link pairs with canonical
   records; prove blocked sibling fields stayed unchanged.
5. Reconcile every eligible target field as ready, `not_distributed`, skipped with an allowed
   reason, or blocked.
6. Require every ready and policy-approved `not_distributed` field to be verified.
7. Verify document structure/content policy/sharing.
8. Retry unexplained mismatches once from fresh state.

Completion:

```text
scanned_data_rows = total_data_rows
eligible_keys_after = frozen_eligible_keys
eligible_fields = ready_fields + not_distributed_fields + skipped_fields + blocked_fields
verified_ready_fields = ready_fields + not_distributed_fields
unexplained_mismatches = 0
```

A zero-mismatch subset is not completion when eligible records are missing.

## Failure boundaries

Stop the affected side effect when:

- frozen scope or source snapshot changed materially;
- a target is read-only or transport budget expired;
- exact keys duplicate or identity remains ambiguous;
- value/link policy is unresolved;
- provider/capability is unavailable;
- a document edit is format-unsafe or violates external-content policy;
- mutation outcome is unknown and actual state cannot be read;
- full-scope verification is unavailable.

Continue independent stages/entities when safe. Preserve the compact checkpoint and report failed stage, attempts, verified work, unknown state, budget exception, and exact resume action.
