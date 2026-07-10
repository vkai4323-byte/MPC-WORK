# Tool routing

## Mandatory routes

| Target or action | Primary route | Fallback |
|---|---|---|
| Read/write POPO | `$popo-sheet` + `$kimi-webbridge` | POPO skill's verified UI fallback |
| Public Bilibili video details or keyword search | `scripts/bilibili_batch.py` using `bilibili-api-python` | `$kimi-webbridge` for unresolved items only |
| Login-dependent Bilibili, Douyin, Xiaohongshu, Kuaishou | `$kimi-webbridge` | None without disclosure |
| Feishu documents | fixed local `feishu-doc.ps1` | Stop; do not browser-edit |
| `.xlsx`, `.csv`, `.tsv` | `$spreadsheets` | None |
| Public non-login facts | built-in web research or a suitable API | `$kimi-webbridge` when user-visible state matters |

Load the full instructions for every selected skill before action.

## Bilibili API-first route

Repository: `https://github.com/kiakun-collab/bilibili-api`

Package: `bilibili-api-python` plus one async backend such as `httpx`. The library supports BV/AV IDs, video information, and search; requests are asynchronous and excessive concurrency may trigger `412`.

Use:

```powershell
python scripts/bilibili_batch.py --input bilibili-items.json --output bilibili-results.json
python scripts/bilibili_batch.py --search "关键词" --page 1 --output bilibili-search.json
```

Input may be a JSON array of strings or objects containing `key`, `url`, `bvid`, or `aid`. Output preserves `key` and includes canonical URL, title, owner, play count, observation time, status, and error.

Routing rules:

1. Extract and deduplicate BV/AV IDs from the filtered sheet rows.
2. Run one batch with concurrency 2 by default.
3. On rate-limit/`412`, retry with bounded backoff and serial concurrency.
4. Validate video identity using BVID plus title/owner when the sheet provides them.
5. Send only failed or ambiguous records to Kimi WebBridge.

Do not require browser screenshots for API-returned public counts unless the user asks for them.

## POPO through Kimi WebBridge

For a `docs.popo.netease.com` URL:

1. Reuse one Kimi session and one editable tab.
2. Fetch a ShareDB snapshot and extract only the named tab's required columns.
3. Match by the live visible key and retain internal row/column IDs only for the current snapshot version.
4. Before writing, refresh once; retry transient snapshot/WebSocket failures at most twice with short backoff.
5. Batch write current targets, then verify with one new snapshot.

Do not replace structured reads with scroll-and-screenshot loops. Do not rerun Bilibili research after a POPO transport retry.

## Authenticated social research

Use `$kimi-webbridge` when data depends on login/cookies, client-rendered UI, private state, or a field unavailable from the chosen API. Confirm identity with name plus an ID/secondary signal. Capture URL and observation time. Screenshot only when requested or needed to resolve ambiguity.

## Feishu CLI

Fixed entry point:

```text
C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools\feishu-doc.ps1
```

Run from its containing directory. Never expose `.env`. Always dry-run replacement maps, read back after edits, and verify permission changes. If unavailable, stop rather than substituting browser editing.

## Preflight

Internally map each selected module to its tool. Verify fixed executables and API dependencies before the first side effect. If a dependency is missing, request installation permission once and name the exact package; do not spend time attempting unrelated fallbacks.
