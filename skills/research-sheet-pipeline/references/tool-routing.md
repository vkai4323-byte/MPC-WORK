# Tool routing

## Contents

- Capability ownership
- Preflight and process lifecycle
- Bilibili API-first route
- POPO through the owning adapter
- Authenticated social research
- Portable Feishu provider
- Output and artifact budget

## Capability ownership

| Target or action | Owning capability / primary route | Bounded fallback |
|---|---|---|
| Structured POPO read/write | `$popo-sheet` with one authenticated `$kimi-webbridge` session | POPO skill's verified UI fallback |
| Public Bilibili videos, creators, or search | `scripts/bilibili_batch.py` | `$kimi-webbridge` for unresolved items only |
| Authenticated Douyin/Xingtu data | `$douyin-xingtu` with its authenticated browser session | Stop the Xingtu stage if login is unavailable |
| Other login-dependent social research | `$kimi-webbridge` | Stop with disclosure if login is unavailable |
| Any Feishu online document | official `lark-cli` through `scripts/document_provider.py` | bundled CLI adapter for compatible operations; otherwise stop the affected stage |
| `.xlsx`, `.csv`, `.tsv` | `$spreadsheets` | None |
| Public non-login facts | Suitable API or built-in web research | Browser only when user-visible state matters |

Load every selected skill's full instructions before action. The pipeline composes stages; it does not take ownership of a platform's private protocol.

🛑 Never reconstruct/replay POPO ShareDB/WebSocket messages or Xingtu endpoints from this pipeline. If the owning adapter cannot provide the needed structured capability within budget, stop that stage and checkpoint it.

## Preflight and process lifecycle

Map each module to one primary tool before the first side effect:

- Bilibili: resolve workspace Python, then run `python scripts/bilibili_batch.py --self-test` from this skill directory. Do not probe a different global interpreter.
- Douyin/Xingtu: load `$douyin-xingtu`, run `python scripts/xingtu_batch.py self-test` once from its directory, and reuse its authenticated session.
- Feishu: read `feishu-cli.md` and `document-providers.md`, resolve the official CLI, then read its embedded version-matched domain skill. Keep tool capability, account scope, document ACL, and target preflight as separate checks. Never expose a command path, profile, `.env`, token, or secret.
- Browser/sheet: start one task session, open the supplied POPO URL with `newTab:true`, then capability-check the exact structured read/write verbs on that fresh tab. Do not borrow an earlier POPO tab for mutation. If an old tab is offline/read-only, open the original URL once in a new task tab; if a verb is absent there, stop the stage.
- If a shell call yields a cell ID, wait immediately. Poll at most 60 seconds at a time; after two no-progress polls, terminate it and take one stated fallback.

Source routing is additive: a manifest containing both Bilibili and Douyin/Xingtu must schedule both `bilibili_batch.py` and `$douyin-xingtu`. Detecting one source must not replace the other.

Maintain one attempt ledger per stage. Switching from adapter to browser, snapshot to screenshot, or one task turn to another does not create a new retry budget. Preserve completed upstream results.

## Bilibili API-first route

The bundled script prepends `.deps` and uses `bilibili-api-python` with an async HTTP backend.

```powershell
python scripts/bilibili_batch.py --self-test
python scripts/bilibili_batch.py --input bilibili-items.json --output bilibili-results.json
python scripts/bilibili_batch.py --search "关键词" --page 1 --output bilibili-search.json
python scripts/bilibili_batch.py --creator-input creators.json --recent 5 --output creators-results.json
python scripts/bilibili_batch.py --user-search "达人名称" --page 1 --page-size 20 --output creator-candidates.json
```

Video input: strings or keyed objects containing `url`, `bvid`, or `aid`.

Creator input: keyed objects containing `mid` or a `space.bilibili.com/<mid>` URL. Bare numeric strings are rejected to avoid MID/AID confusion.

Routing:

1. Freeze exact sheet `source_key` values before extracting/deduplicating video IDs or MIDs.
2. Use concurrency 2.
3. For name discovery, confirm exact normalized name plus MID; never auto-select ambiguity.
4. On `412`, retry once serially. If fewer than requested recent uploads are available, mark `partial`.
5. Validate video with BVID + title/owner and creator with name + MID.
6. Send only failed/ambiguous records to the browser.
7. For known-video refreshes, preserve the input `source_key`, resolve a `b23.tv` short link once,
   and return play, like, comment, share, and favorite counts from the video detail `stat` object.

Do not take screenshots for public API counts unless requested.

## POPO through the owning adapter

### Read-only

1. Start one authenticated task session and open the supplied URL in a fresh tab.
2. Fetch one structured snapshot, scan every row ID, then filter the named tab/required columns.
3. Copy writable `source_key` values from structured cells or an exact structured export.
4. For `disposable_login_token=1`, do not reload a stale tab. If an earlier tab is offline/read-only,
   open the original URL once in a new task tab and continue only there.
5. Use at most one fresh-tab recovery and 90 seconds total.
6. If the fresh tab is unavailable, checkpoint independent work and request a renewed link only if indispensable.

Do not:

- keep diagnosing or refreshing an offline/read-only old tab after the fresh-tab recovery;
- replace structured key acquisition with scroll/screenshot/OCR;
- reconstruct ShareDB/WebSocket traffic;
- rerun source research after POPO transport failure.

### Writeback

1. Run the live gate from `module-contracts.md`.
2. Fetch a fresh structured snapshot, scan all data rows before filtering, and match exact live `source_key`.
3. Keep internal row/column IDs only for that snapshot version.
4. Reconcile full frozen eligibility coverage.
5. Batch current targets with preconditions.
6. Verify exact values and the full eligible-key set with one fresh full-row scan.

If acknowledgement is unknown, lock the destination and read actual state before retrying. Never start a second writer while the first process/outcome is unresolved.

## Authenticated social research

Use `$kimi-webbridge` only when data depends on login/cookies, private client state, or a field absent from the chosen adapter. Confirm identity with name plus ID/secondary signal. Capture URL and observation time. Screenshot only on request or to resolve ambiguity.

For Douyin/Xingtu, pass known item URLs/IDs to `$douyin-xingtu` in one batch. Preserve:

- exact sheet `source_key`;
- item, author/core-user, and star identity fields;
- adapter status;
- endpoint, returned source field, observation time, and alternative observations.

Only exact `ready` fields and policy-approved `not_distributed` fields are writable. A
field-level `metric_conflict` blocks that field; `ambiguous`/`identity_conflict` blocks the row.
Never downgrade any of them to a name-only match, and never suppress unrelated ready fields.

## Official Feishu CLI provider

Run from this skill directory:

```powershell
python scripts/document_provider.py --format json
```

Use a `ready` official CLI result and still perform a read-only preflight against the exact
resource. `doctor` proves configuration and identity availability, not every scope or document
ACL. Require the exact job capabilities and keep tool support, granted scope, and target access
as separate states.

Invoke every Feishu domain through the same resolved provider:

```powershell
python scripts/document_provider.py --run -- drive +inspect --url "<feishu-url>"
python scripts/document_provider.py --run -- docs +fetch --doc "<doc-url>"
python scripts/document_provider.py --run -- sheets +workbook-info --url "<wiki-or-sheet-url>"
python scripts/document_provider.py --run -- sheets +csv-get --url "<wiki-or-sheet-url>" --sheet-id "<exact-id>" --range "A1:Z200"
```

Before using a domain, run `lark-cli skills read lark-shared`, then read the matching embedded
skill and every operation-specific reference it names. Prefer `+` shortcuts, then typed API
commands, then the official CLI's raw `api` escape hatch. For Sheets, start with
`+workbook-info`, use a returned stable `sheet_id`, prefer `+batch-update` for related mutations,
and read back every changed range.

If no CLI route covers the requested action, stop only the affected Feishu stage.

The official CLI provides structured JSON, schema discovery, risk levels, dry-run, versioned
skills, and raw OpenAPI access. The bundled `scripts/feishu_doc.py` contains no credentials and
remains only a compatibility fallback.

Browser reading or editing is never a fallback for Feishu online-document work.

## Output and artifact budget

Keep fast-path state in memory. For complex/resumable jobs, use the configured relative artifact directory.

Normal output allowlist:

- `checkpoint.json`;
- `change-plan.json`;
- `verification-summary.json`.

Return compact counts, exact IDs/keys, unmatched items, and errors. Do not print full workbook snapshots, document blocks, provider locators, protocol payloads, or dry-run maps unless failure debugging is explicitly enabled. Never persist credentials.

Use `python scripts/checkpoint_artifacts.py write-checkpoint --input - --output <artifact-dir>/checkpoint.json` for a redacted, integrity-hashed atomic replacement; the filename is not configurable. Provide the complete compose-valid v3 manifest on standard input so no raw intermediate is stored in the artifact directory. Then run `python scripts/checkpoint_artifacts.py verify-dir --artifact-dir <artifact-dir> --mode <mode> --max-debug-files <n> --debug-retention-days <days>`. The verifier scans text/JSON for unredacted structured secrets, token-bearing URLs, credentials in free text, emails, and private keys. Under `minimal`, any non-allowlisted file is an error. Debug modes enforce the file cap and report stale artifacts; the helper never deletes files.

Sensitive redaction is mandatory. If the exact writable key itself is protected data, stop persistence rather than replacing it with a redacted key. When debug artifacts are enabled, honor the manifest's file cap and retention period and redact disposable-login tokens, cookies, emails, credential material, and internal collection/session IDs before persistence.
