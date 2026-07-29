---
name: popo-sheet
description: Operate NetEase POPO / office.netease.com online spreadsheets through the user's authenticated Kimi WebBridge browser session. Use for POPO sheet URLs, structured cell reads, exact-key and field-level batch fills, multi-platform metric refreshes, formulas, hyperlinks, row/column sizing, borders, wrap, formatting, sheet tabs, and safe UI fallback on canvas-rendered grids.
---

# POPO Sheet via Kimi WebBridge

POPO grids are canvas-rendered inside a cross-origin `office.netease.com` iframe.

## Pick one lane

| Task | Lane |
|---|---|
| Read/replace plain values or links | structured fast lane |
| Sizing, borders, wrap, merges, layout, unknown schema | UI lane |
| Structured access unavailable | one bounded UI/clipboard fallback |

Do not run both lanes “for safety”. A structurally verified value write needs no screenshot, focus
probe, clipboard test, or per-cell click.

## Make each target field independent

Use the target cell/field—not the whole row—as the default readiness and write unit. Freeze each
field with its exact row key, target column, desired value, evidence/source, observation time, and
one status:

- `ready`: exact value is available and writable;
- `not_distributed`: absence is proven and the declared policy permits writing `0`;
- `blocked`: a source/link exists but the value is unavailable, ambiguous, or conflicting.

Write `ready` and policy-approved `not_distributed` fields even when another field in the same row is
`blocked`. Leave blocked fields unchanged and report them by row plus platform/column. Block the
whole row only when the row identity/exact key is ambiguous, duplicated, or changed, or when the user
explicitly declares a row-atomic contract.

Do not treat “unreadable” as “not distributed”. Do not infer “latest” from the largest number. Use
the declared authoritative source and compare `observed_at` when timestamps exist.

## Fresh task tab — mandatory for mutation

1. Use one Kimi WebBridge task session.
2. Open the current user-supplied POPO URL with `newTab:true`.
3. Never borrow or refresh an old tab showing `只读`, `已离线`, or reconnecting.
4. Continue only when the new page has an office iframe and no offline/read-only banner.
5. Recheck immediately before write and readback. If the bound task tab has since gone offline,
   leave it untouched and open one fresh replacement with `newTab:true`.
6. If that replacement also fails or its disposable link expired, stop the sheet stage. Never
   refresh an offline tab or create repeated replacement chains.

## Structured fast lane

Load only `references/structured-fast-lane.md`.

### S0 — full read, then filter

Resolve the requested live tab and headers, enumerate every structured data-row ID, and only then
apply the eligibility predicate.

```text
scanned_row_ids = live_data_row_ids
scanned_data_rows = total_data_rows
filter_after_full_scan = true
```

Freeze internal `row_id`, byte-for-byte `source_key`, platform/source values, per-field targets,
evidence/`observed_at`, and policy. Matches may be non-contiguous. Stop on partial scan,
blank/duplicate exact key, duplicate row ID, missing header, or read-only/protected state.

Never use viewport rows, first matching block, fixed upper bounds, `slice`, early `break`, screenshots,
OCR, visual row numbers, or normalized names to form writable scope.

### S1 — one conditional batch

Immediately before writing:

1. Fetch a new snapshot/version and repeat the full scan.
2. Require live and frozen eligible key sets to be exactly equal in both directions.
3. Resolve current sheet/row/column IDs and old values from that snapshot.
4. Build one JSON0 batch for every approved `ready`/`not_distributed` field, preserving blocked and
   non-target fields and all styles.
5. Submit once with old-value preconditions.

On unknown acknowledgement, keep one writer and read actual state before one delta retry. Never reuse
IDs/version after transport failure.

`scripts/name_match_tsv.py` is only a rectangular clipboard fallback. It is not a writer or proof of
full-sheet coverage, and its relative row numbers are not sheet row IDs.

### S2 — full structural readback

Fetch another snapshot, repeat the full scan, and compare every requested key/value pair.

```text
scanned_row_ids = live_data_row_ids
eligible_keys_after = frozen_eligible_keys
eligible_fields = ready_fields + not_distributed_fields + blocked_fields
verified_ready_fields = ready_fields + not_distributed_fields
unexplained_mismatches = 0
```

Derive row and field denominators from the live post-scan scope, never from the write plan. Skip
screenshots after a successful plain value/link verification.

## UI lane

Load only the relevant section of `references/popo-sheet-reference.md`.

1. Capture one orientation screenshot.
2. Focus the office iframe and verify one harmless movement.
3. Apply the smallest supported grid action.
4. For clipboard fallback, prove the selected rectangle and copied TSV before paste.
5. Stop on protected/read-only toast, shifted paste, unknown focus, or empty clipboard.
6. Copy/read values back; take a final screenshot only when visual formatting matters.

Infer formatting from nearby rows/columns: height, width, wrap, alignment, fills, font, borders,
number/date format, and hyperlink convention.

## Hard stops

- Never mutate from an old offline/read-only page.
- Never filter a partial row sample or assume equal dates form one block.
- Never use external row numbers, OCR, screenshots, or normalized names as keys.
- Never use visual row/column numbers as ShareDB cell IDs; resolve IDs through `sheet.rows[]` and
  `sheet.cols[]`.
- Never send fetch/op before the ShareDB `init` message.
- Never let one blocked platform/field suppress verified fields in the same row unless the contract
  is explicitly row-atomic.
- Never write `0` for an unreadable linked distribution.
- Never click hyperlink cells unless the user asked to open them.
- Never continue after a protected/read-only warning.
- Never start a second writer while the first result is unknown.
- Never report completion from ACK, screenshot, or subset `mismatches=0`.

## Windows helper

Use `scripts/webbridge_command.ps1` for large WebBridge calls. It writes UTF-8 without BOM and saves
large responses outside tool stdout.
