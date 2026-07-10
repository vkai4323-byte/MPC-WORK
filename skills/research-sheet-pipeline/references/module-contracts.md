# Module contracts

## Contents

- Scope
- Sheet context and schema
- Web research
- Normalize
- Document
- Sheet create and writeback
- Verify
- Failure boundaries

## Scope

Inputs: user objective, known entities or discovery rule, requested fields, output destination.

Outputs: job manifest with explicit module switches, entity key, target platform, evidence policy, and completion criteria.

Default evidence policy for browser research: source URL plus one useful screenshot per entity or page state. Record the observation time for unstable facts.

## Sheet context and schema

For an existing sheet:

1. Read the latest structured state.
2. Select the requested tab by title.
3. Infer the visible key column and target columns from headers and nearby completed rows.
4. Record current values and local formatting conventions.
5. Use normalized visible keys for later joins.

For a new sheet:

1. Draft the minimum useful schema from requested outputs.
2. Include one stable key column and evidence/source fields.
3. Add document-link columns only if documents are enabled.
4. Define types, required fields, blank policy, and hyperlink display convention.
5. Create the sheet only after the schema is internally consistent.

Sheet-context output:

```json
{
  "platform": "popo",
  "sheet": "执行",
  "key_column": "达人昵称",
  "columns": [{"name": "粉丝数", "type": "number"}],
  "format_reference": "nearest completed row",
  "snapshot_version": "latest"
}
```

## Web research

Use one browser session per job. For each entity:

1. Navigate to the authoritative or user-requested page.
2. Confirm identity using name plus a secondary identifier when possible.
3. Capture only requested fields and relevant evidence.
4. Store the source URL, screenshot path, observation time, and any uncertainty.
5. Do not silently merge similarly named entities.

If login, CAPTCHA, or missing pages block only some entities, continue the others and mark blocked records. Do not fabricate values.

## Normalize

Transform research output into canonical records. Normalize whitespace and harmless punctuation, but preserve display names. Convert dates and counts to the target sheet's convention. Keep original text in evidence when normalization could hide meaning.

Checkpoint report:

- source records;
- unique keys;
- exact and normalized matches;
- duplicates;
- unresolved required fields;
- records ready to write.

Stop before side effects if duplicates or ambiguous matches affect targeted rows.

## Document

Run only when `documents.mode` is `create` or `update`.

Inputs: canonical record, named template/source document, naming rule, common brief, entity-specific direction, release node, and permission rule.

Procedure:

1. Read the template with the designated document tool.
2. Copy rather than rebuild when the user asks to preserve the exact format.
3. Apply common replacements and entity-specific content separately.
4. Set requested sharing permissions through the same designated tool.
5. Read back title, key facts, block counts or equivalent structure, and sharing state.
6. Add the verified document URL to `record.documents`.

Never create a document merely because a link column exists; the job contract must enable the module.

## Sheet create and writeback

Creation:

1. Create the workbook or sheet on the specified platform.
2. Add headers, types, widths, wrapping, filters, and baseline formats.
3. Verify the empty schema before loading records.

Writeback modes:

- `append`: add new records after the current data region.
- `fill`: update requested blank columns for matched existing rows.
- `upsert`: update unique matches and append missing keys.

Rules:

1. Join on the live sheet's key values, never external row numbers.
2. Re-read immediately before writing.
3. Preserve non-target fields and existing styles.
4. Match nearby hyperlink display conventions.
5. Split around protected or conflicting rows.
6. For large fills, write contiguous blocks after producing a match report.

## Verify

Verify structure first, presentation second:

1. Freshly read the written range or workbook snapshot.
2. Compare exact key/value/link pairs against canonical records.
3. Reconcile inserted, updated, skipped, and failed counts.
4. Confirm permissions for created documents.
5. Screenshot only the useful final state when formatting or visual proof matters.

Completion requires zero unexplained mismatches.

## Failure boundaries

Stop writes when the schema changed, a target is read-only, duplicate keys exist, a non-empty cell conflicts, or a verification read is unavailable. Preserve completed independent records and report the exact boundary. Retry rate-limited APIs serially after reading actual state; do not replay the whole batch blindly.
