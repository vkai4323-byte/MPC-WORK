# Tool routing

## Contents

- Mandatory routing table
- POPO through Kimi WebBridge
- Social profile research
- Feishu CLI
- Other spreadsheet outputs
- Preflight and fallback rules

## Mandatory routing table

| Target or action | Required tools | Do not substitute |
|---|---|---|
| Read, create, or edit a POPO sheet | `$popo-sheet` + `$kimi-webbridge` | Generic web search, DOM-only browser tools, OpenClaw |
| Read or screenshot Douyin/Bilibili/Xiaohongshu/Kuaishou pages | `$kimi-webbridge` in the user's Edge session | Search snippets when the actual profile is available |
| Read, copy, create, edit, or set permissions on Feishu documents | Local `feishu-doc.ps1` | OpenClaw, Feishu browser editing |
| Create or edit `.xlsx`, `.csv`, `.tsv` files | `$spreadsheets` | POPO browser operations |
| Public, non-login factual research | Built-in web research or `$kimi-webbridge` when the user requires it | Logged-out approximations for login-gated sources |

Load the full instructions for every selected skill before action. A POPO job always selects two skills: `$popo-sheet` supplies workbook rules and `$kimi-webbridge` supplies the authenticated browser transport.

## POPO through Kimi WebBridge

For a `docs.popo.netease.com` URL:

1. Load `$popo-sheet` and `$kimi-webbridge`.
2. Use one Kimi session for the entire job.
3. Bind the user's selected Edge tab with `find_tab(active:true)` when the user says the sheet is already open; otherwise navigate to the supplied URL.
4. Confirm an editable authenticated page before reading or writing.
5. Use the `$popo-sheet` ShareDB snapshot path for authoritative headers, keys, values, and versions.
6. Match records by the live visible key column.
7. Write with the `$popo-sheet` safe path and verify using a fresh snapshot.

Kimi WebBridge is the transport; `$popo-sheet` is the spreadsheet protocol and safety layer. Neither replaces the other.

## Social profile research

Use `$kimi-webbridge` for Douyin, Bilibili, Xiaohongshu, Kuaishou, Weibo, and other pages whose useful content depends on login, cookies, client rendering, or user-visible state.

For each entity:

1. Bind or open the page in the same Edge session.
2. Confirm the profile name plus an ID or secondary signal.
3. Read requested fields from the actual page.
4. Take the requested homepage screenshot and preserve the returned file path.
5. Store URL, screenshot, observation time, and uncertainty in the canonical record.

Do not replace an accessible real profile with search-engine summaries. Use public web search only for discovery or corroboration when the user has not required Kimi-only research.

## Feishu CLI

Fixed installation:

```text
Directory: C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools
Entry point: C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools\feishu-doc.ps1
Config:      C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools\.env
```

Preflight:

```powershell
$dir = 'C:\Users\Admin\Documents\Codex\2026-07-01\new-chat\outputs\feishu-doc-tools'
$cli = Join-Path $dir 'feishu-doc.ps1'
if (-not (Test-Path -LiteralPath $cli)) { throw "Feishu CLI not found: $cli" }
$env:PYTHONIOENCODING = 'utf-8'
```

Run all commands with the CLI directory as the shell working directory. Never print or expose `.env` values.

Command mapping:

| Intent | Command |
|---|---|
| Resolve wiki to docx | `.\feishu-doc.ps1 resolve "WIKI_URL"` |
| Read text | `.\feishu-doc.ps1 raw "DOCUMENT_ID"` |
| Inspect blocks | `.\feishu-doc.ps1 blocks "DOCUMENT_ID"` |
| Copy a wiki template | `.\feishu-doc.ps1 wiki-copy "WIKI_URL" "NEW_TITLE"` |
| Copy docx to writable drive | `.\feishu-doc.ps1 drive-copy "DOCUMENT_ID" "NEW_TITLE"` |
| Preview replacements | `.\feishu-doc.ps1 replace-map "DOCUMENT_ID" ".\replacements.json" --dry-run` |
| Apply replacements | `.\feishu-doc.ps1 replace-map "DOCUMENT_ID" ".\replacements.json"` |
| Read sharing permission | `.\feishu-doc.ps1 permission-get "DOCUMENT_ID" --type docx` |
| Set anyone-with-link editable | `.\feishu-doc.ps1 permission-anyone-edit "DOCUMENT_ID" --type docx` |
| Append a paragraph | `.\feishu-doc.ps1 append "DOCUMENT_ID" "TEXT"` |

Always run `replace-map --dry-run` before applying replacements. Read back content after edits and call `permission-get` after permission updates. If the CLI path or command is missing, stop and report the missing capability; do not use OpenClaw or browser editing as a fallback.

## Other spreadsheet outputs

Use `$spreadsheets` for local workbook files. Use `$popo-sheet` only for live POPO workbooks. If the user asks for a new online sheet but does not name the platform, ask for the platform because that choice changes the execution tool and sharing model.

## Preflight and fallback rules

Before the first side effect, record a tool plan such as:

```text
sheet_context: kimi-webbridge + popo-sheet
web_research: kimi-webbridge (Edge)
document: feishu-doc.ps1
sheet_writeback: kimi-webbridge + popo-sheet
verify: POPO fresh snapshot + Feishu CLI read-back
```

If Kimi WebBridge is not running, follow its skill instructions to start the daemon and retry once. If the Edge tab is not authenticated, ask the user to open or focus the authorized page. Never downgrade to a logged-out result without disclosure.
