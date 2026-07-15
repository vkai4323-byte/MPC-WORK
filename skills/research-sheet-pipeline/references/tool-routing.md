# Tool routing

## Mandatory routes

| Target or action | Primary route | Bounded fallback |
|---|---|---|
| Read/write POPO | `$popo-sheet` + `$kimi-webbridge` | POPO skill's verified UI fallback |
| Public Bilibili videos, creators, or search | `scripts/bilibili_batch.py` | `$kimi-webbridge` for unresolved items only |
| Login-dependent Bilibili, Douyin, Xiaohongshu, Kuaishou | `$kimi-webbridge` | Stop with disclosure if login state is unavailable |
| Feishu documents | `scripts/document_provider.py` + a ready CLI | Authenticated Agent connector with equivalent capabilities; otherwise stop `document` |
| `.xlsx`, `.csv`, `.tsv` | `$spreadsheets` | None |
| Public non-login facts | built-in web research or a suitable API | `$kimi-webbridge` when user-visible state matters |

Load the full instructions for every selected skill before action.

## Preflight and process lifecycle

Map each selected module to one primary tool before the first side effect.

- Bilibili: resolve a working workspace Python first, then run `python scripts/bilibili_batch.py --self-test` from the skill directory. If PATH resolves to the Windows Store shim, use the Python returned by the workspace dependency loader. The self-test accepts either the ignored `.deps` directory or packages installed from `requirements.txt`; do not probe a different interpreter.
- Feishu: read `references/document-providers.md`, run the provider resolver, and keep manifest validity separate from provider readiness. Never expose a command path, `.env`, or secret in tool output.
- Browser/sheet: reuse the current authenticated task session rather than creating a test tab.
- If a shell call yields a cell ID, call `wait` immediately. Poll at most 60 seconds at a time. After two polls with no progress, terminate the process and take one stated fallback.

Do not stack multiple diagnostic routes for the same failure. Preserve completed upstream results.

## Bilibili API-first route

The bundled script prepends `.deps` and uses `bilibili-api-python` with an async HTTP backend.

```powershell
python scripts/bilibili_batch.py --self-test
python scripts/bilibili_batch.py --input bilibili-items.json --output bilibili-results.json
python scripts/bilibili_batch.py --search "关键词" --page 1 --output bilibili-search.json
python scripts/bilibili_batch.py --creator-input creators.json --recent 5 --output creators-results.json
python scripts/bilibili_batch.py --user-search "达人名称" --page 1 --page-size 20 --output creator-candidates.json
```

Video input is a JSON array of strings or objects containing `key`, `url`, `bvid`, or `aid`.

Creator input is a JSON array of keyed objects containing `mid` or a `space.bilibili.com/<mid>` URL. Space-URL strings are accepted; bare numeric strings are rejected to avoid confusing creator MID with the backward-compatible video AID form.

Routing:

1. Extract and deduplicate video IDs or creator MIDs after filtering sheet rows.
2. Use concurrency 2.
3. For creator-name discovery, list user-search candidates and confirm normalized name plus MID; do not auto-select an ambiguous name.
4. On `412`, retry once serially. Creator lookup may then use the exact-MID candidate returned by user search; if fewer than `N` recent uploads are available, keep the result as `partial`.
5. Validate video identity with BVID + title/owner and creator identity with name + MID.
6. Send only failed or ambiguous records to Kimi WebBridge.

Do not take browser screenshots for public API-returned counts unless requested.

## POPO through Kimi WebBridge

### Read-only

1. Reuse one authenticated session and one task tab.
2. Fetch one structured snapshot and extract only the named tab's required columns.
3. For `disposable_login_token=1`, never navigate the URL twice; recheck the already-open tab once.
4. Use at most two acquisition attempts, at most one new task session, and 90 seconds total.
5. If acquisition fails, continue independent research and request a renewed link only if the sheet is indispensable.

Do not confirm the same unavailable state through an evaluate → snapshot → screenshot → new-tab cascade.

### Writeback

1. Run the live editability/online gate from `references/module-contracts.md`.
2. Fetch one fresh snapshot and match by visible key.
3. Keep internal row/column IDs only for that snapshot version.
4. Batch current targets with preconditions.
5. Verify exact values with one new compact snapshot.

Do not replace structured reads with scroll-and-screenshot loops. Do not rerun source research after a POPO transport retry.

## Authenticated social research

Use `$kimi-webbridge` only when data depends on login/cookies, client-rendered private state, or a field unavailable from the chosen API. Confirm identity with name plus an ID/secondary signal. Capture URL and observation time. Screenshot only when requested or needed to resolve ambiguity.

## Portable Feishu provider

Run from the skill directory:

```powershell
python scripts/document_provider.py --format json
```

Use a `ready` bundled CLI result, or a `legacy_unverified` external CLI only after a read-only target and capability preflight. If the resolver reports `needs_credentials` or `unavailable`, capability-check an authenticated document connector exposed to the Agent. Require only the exact job capabilities; for sharing, distinguish public-permission read and `anyone_editable` from arbitrary permission writes. If no route covers copy, grouped replacement/dry-run, requested permission operation, read-back, and structural signature, stop only the document stage.

The bundled `scripts/feishu_doc.py` contains no credentials. Run its `doctor` command before mutation. Never ask for or print `FEISHU_APP_SECRET`; instruct the user to configure it locally. Always dry-run replacement maps. The apply command batches at most 200 affected blocks with an idempotency token and verifies exact content plus the inline-style-aware structural signature; separately verify any permission change. Never substitute browser editing.

## Output and artifact budget

Keep fast-path state in memory. For complex/resumable jobs, use `.codex-runs/research-sheet-pipeline/<job-id>/`. Return compact counts, IDs, unmatched items, and errors; do not print full workbook snapshots, full blocks payloads, full provider locators, or full dry-run change maps into the conversation unless debugging is requested.
