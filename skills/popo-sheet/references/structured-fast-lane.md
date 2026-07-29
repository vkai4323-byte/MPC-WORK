# POPO structured fast lane

Use this route only for reading or replacing ordinary cell values and hyperlinks. Formatting,
formulas, merges, protected ranges, row/column sizing, and unknown schemas belong to the UI fallback.

## Inputs

Freeze these before research:

- current user-supplied POPO URL and requested tab title;
- structured eligibility predicate;
- exact key column;
- platform/source URL or ID columns;
- target columns;
- per-target evidence source and `observed_at` when available;
- `overwrite` for refresh or `preserve_nonempty` for fill-missing.

Default to field-atomic execution. Give every target field one status:

- `ready`: exact value available;
- `not_distributed`: absence proven and policy permits `0`;
- `blocked`: linked source exists but is unreadable, ambiguous, conflicting, or missing.

Do not block verified fields because a sibling platform/field is blocked. Use a row-atomic contract
only when the user or schema explicitly makes the fields inseparable.

## 0. Open one fresh task tab

1. Start/reuse one Kimi WebBridge task session.
2. Navigate the supplied URL with `newTab:true`.
3. Never mutate an old tab showing `只读`, `已离线`, or reconnecting.
4. Require an `office.netease.com` iframe and no offline/read-only banner.
5. Recheck before S1 and S2. If the bound tab has gone offline, leave it untouched and open one fresh
   replacement with `newTab:true`.
6. If the replacement fails or the link expired, stop the sheet stage.

Do not reload an offline tab or create a chain of repeated replacements.

## S0. Full structured snapshot

From the top frame:

1. Read the office iframe URL and its `identity`, `source`, and locale.
2. Connect to the office ShareDB WebSocket.
3. Record `collectionID`/`docID` from `begin`, wait for ShareDB `init`, then send the fetch. Requests
   sent before `init` may be silently dropped.
4. Use a configurable snapshot timeout with a 45,000 ms default. Track
   `open/begin/init/fetch-sent/fetch-recv`; include the stages in timeout/error messages. Large
   workbooks can need more than 30 seconds.
5. Keep the returned workbook plus version `v`; do not print or persist the full snapshot.
6. Resolve the requested live tab by ID/title. `workbook.tabs` is the visual-order array of sheet ID
   strings.
7. Resolve headers from the live header row and columns vector.
8. Resolve cell keys only through internal IDs: visual row `r` maps to `sheet.rows[r]`; visual column
   `c` maps to `sheet.cols[c]`. Never compose keys from visual indexes.
9. Set `live_data_row_ids` to every row ID after the header.
10. Visit every ID exactly once, then assert:

```text
scanned_row_ids = live_data_row_ids
scanned_data_rows = total_data_rows
filter_after_full_scan = true
```

Only now evaluate the predicate. For “发布链接非空”, use the cell's visible value and link metadata
consistently; whitespace-only values are empty.

Freeze each match as:

```json
{
  "row_id": "internal structured row id",
  "source_key": "byte-for-byte sheet value",
  "platform": "sheet value",
  "publish_url": "sheet value or link metadata",
  "fields": {
    "target header": {
      "status": "ready | not_distributed | blocked",
      "value": "exact desired value when writable",
      "source": "authoritative source",
      "observed_at": "source observation time",
      "reason": "required when blocked"
    }
  }
}
```

Stop on a duplicate/blank exact key, duplicate row ID, missing header, or partial scan. Row indexes are
diagnostics only. Matches may be non-contiguous. A row identity failure blocks the row; a single
field-source failure blocks only that field.

## S1. One conditional batch write

Immediately before writing, fetch a new snapshot and:

1. Repeat the complete scan and predicate independently.
2. Require both set differences to be empty:

```text
live_eligible_keys - frozen_eligible_keys = empty
frozen_eligible_keys - live_eligible_keys = empty
```

3. Resolve current sheet, row, column IDs, values, and version from this snapshot.
4. Build one JSON0 op for all `ready` fields and policy-approved `not_distributed` fields. Preserve
   blocked fields unchanged.
5. For an existing field include its old value (`od`); for a blank field omit `od`.
6. Change only value/link fields and preserve style keys.
7. For a hyperlink, follow the live neighboring-cell convention for value and link metadata.
8. Open the writer socket, record `begin`, wait for `init`, then submit one op using the fresh
   version, `clientID`, and one sequence. Use a configurable 45,000 ms op timeout and stage trace.

If acknowledgement is unknown, keep a single writer, fetch actual state, and retry only unexplained
delta once. Never reuse version or internal IDs after a transport failure.

## S2. Full structural verification

Fetch one more new snapshot. Repeat the complete scan and predicate; then require:

```text
scanned_row_ids = live_data_row_ids
eligible_keys_after = frozen_eligible_keys
eligible_fields = ready_fields + not_distributed_fields + blocked_fields
verified_ready_fields = ready_fields + not_distributed_fields
unexplained_mismatches = 0
```

Compare every writable field for every ready key and prove blocked fields were unchanged. Derive
counts from the live row/field scope, not from the write-plan length. A subset with zero mismatches
is failure, not completion.

Return only: sheet, total/scanned row counts, eligible count and exact keys, field-level
ready/not-distributed/blocked counts, rows/cells updated, unchanged blocked cells, mismatches, and
status. Include unresolved row + platform/column keys. Derive every count from live records.

No screenshot, focus probe, clipboard operation, per-cell click, raw snapshot artifact, manifest, or
checkpoint is needed after successful structural verification.
