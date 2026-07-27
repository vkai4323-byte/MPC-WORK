# POPO Sheet Reference

## Table Of Contents

- Site Structure
- ShareDB Snapshot Read Path
- ShareDB Simple Write Path
- Addressing Internals
- WebBridge Request Files On Windows
- Screenshot And Diagnostic Artifacts
- Keyboard And Clipboard Fallback
- Format Inference Checklist
- Name-Matched Bulk Fill
- Menu Evidence From Live POPO Sheet
- Hyperlinks
- In-Place Edit Mode
- Pixel Click Conversion
- Side Effects And Limits
- Bundled Script Contract
- Engine API Investigation

## Site Structure

- Top frame: `https://docs.popo.netease.com/...`
- Sheet iframe: `office.netease.com`, rendered on `<canvas>`
- Top-frame `evaluate` cannot read cell DOM or iframe internals because of cross-origin restrictions.
- Cell content can be read structurally through clipboard TSV when grid actions implement select +
  `Ctrl+C`.
- The iframe can still receive focus and keyboard input in verified WebBridge/CDP setups. Treat DOM
  inspection as blocked, not keyboard control as automatically blocked.
- CDP keyboard movement and OS clipboard shortcuts can differ. If CDP arrows move the active cell but
  CDP `Ctrl+C` leaves the clipboard empty, keep the selection and switch to OS-level keyboard input.

Fresh task-tab rule:

- At the start of a mutation task, open the current user-supplied POPO URL with `newTab:true`.
- Do not reuse a previously opened POPO tab that is offline, read-only, or reconnecting.
- Open at most one replacement tab. If that tab cannot load or a disposable URL has expired, stop and
  request a current link; do not refresh the stale tab or create a tab chain.

## ShareDB Snapshot Read Path

Use this as the primary read path for POPO sheets when WebBridge can evaluate the top frame. It avoids
mouse coordinates, user selection, and clipboard ambiguity.

Verified facts:

- The top frame can read the office iframe HTML with:
  `fetch(document.querySelector('iframe').src, { credentials: 'include' })`.
- `/api/admin/cowork/query-route?identity=<identity>` returns route metadata.
- `/api/app/cowork/doc?identity=<identity>` returns document metadata.
- The ShareDB WebSocket URL is:
  `wss://office.netease.com/node/?identity=<identity>&serverType=GEZHI&source=POPO_DOC&wantCompress=false&lang=zh-CN`.
- The first WebSocket message is `begin` and includes `collectionID` and `docID`.
- Sending `{"a":"f","c":collectionID,"d":docID}` returns a full workbook snapshot.
- In the returned workbook JSON, sheet cells are under `sheets[sheetId].cells`. Cell keys look like
  `row,col`; text/value is usually in property `"0"`, hyperlink URL/display often uses `"0"` and
  `"1"`, and style/format ids use keys such as `"100"`/`"101"`.

Minimal browser-side probe:

```javascript
async function fetchPopoWorkbookSnapshot() {
  const iframe = document.querySelector("iframe");
  const iframeUrl = new URL(iframe.src);
  const identity = iframeUrl.searchParams.get("identity");
  const source = iframeUrl.searchParams.get("from") || "POPO_DOC";
  const lang = iframeUrl.searchParams.get("popo_locale") || "zh-CN";
  const wsUrl = iframeUrl.origin.replace("https", "wss") + "/node/?" +
    new URLSearchParams({
      identity,
      serverType: "GEZHI",
      source,
      wantCompress: "false",
      lang,
    });

  return await new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => reject(new Error("snapshot timeout")), 10000);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.begin) {
        ws.send(JSON.stringify({ a: "f", c: msg.collectionID, d: msg.docID }));
      } else if (msg.a === "f") {
        clearTimeout(timer);
        ws.close();
        resolve(msg.data.data);
      }
    };
    ws.onerror = () => reject(new Error("snapshot websocket error"));
  });
}
```

Extraction rule:

1. Read `data.tabs` and `data.sheets`.
2. Select the active/requested sheet by tab id or title.
3. Enumerate the complete live data-row ID set from the sheet row vector. Require the scanned ID set
   to equal it before applying any date/status/platform/name/blank filter.
4. Build a sparse table from `sheet.cells`: split each key at `,`, use `cell["0"]` as the visible
   value, and use `cell["1"]` as link/formula metadata when present.
5. Infer header columns from row 0 or the local section header, then match rows by the in-sheet name
   column.

Rows matching one predicate may be non-contiguous. Never use a fixed upper bound, viewport sample,
first matching block, `slice`, or early `break` before filtering.

Fallbacks:

- If the WebSocket returns `MISS_DOC_INFO`, rebuild the URL with `identity`, `serverType=GEZHI`,
  `source=POPO_DOC`, `wantCompress=false`, and `lang`.
- If `export-file` returns `没有导出权限`, do not use export as the primary path.
- If ShareDB snapshot fails, then use grid actions or clipboard TSV fallback.

## ShareDB Simple Write Path

Use this for plain text or hyperlink-style cells when a fresh ShareDB snapshot has already identified
the exact internal sheet id, row id, and column id. This path is safer than UI paste for short,
targeted edits because it does not depend on canvas focus, OS clipboard state, scroll position, or
Name Box behavior.

Validated live behavior:

- An empty op probe with `src: begin.clientID`, `seq: 1`, and the fetched version `v` is ACKed by
  the server.
- A JSON0 op rooted at the workbook document, not at a wrapper `data` property, can set cell fields:
  `["sheets", sheetId, "cells", "<rowId>,<colId>", "0"]`.
- For POPO hyperlink-like cells, set both `"0"` and `"1"` to the URL while preserving existing style
  fields such as `"100"`.
- Re-fetching the snapshot after the op is the verification. Do not take a screenshot after a
  structurally verified value/link write unless the user explicitly requested visual proof; it never
  counts as verification.

Minimal write pattern:

```javascript
async function writePopoSimpleCells(edits) {
  const iframe = document.querySelector("iframe");
  const iframeUrl = new URL(iframe.src);
  const identity = iframeUrl.searchParams.get("identity");
  const source = iframeUrl.searchParams.get("from") || "POPO_DOC";
  const lang = iframeUrl.searchParams.get("popo_locale") || "zh-CN";
  const wsUrl = iframeUrl.origin.replace("https", "wss") + "/node/?" +
    new URLSearchParams({
      identity,
      serverType: "GEZHI",
      source,
      wantCompress: "false",
      lang,
    });

  async function fetchSnapshot() {
    return await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      const timer = setTimeout(() => reject(new Error("snapshot timeout")), 15000);
      let begin = null;
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.begin) {
          begin = msg;
          ws.send(JSON.stringify({ a: "f", c: msg.collectionID, d: msg.docID }));
        } else if (msg.a === "f") {
          clearTimeout(timer);
          ws.close();
          resolve({ begin, fetch: msg });
        }
      };
      ws.onerror = () => reject(new Error("snapshot websocket error"));
    });
  }

  const before = await fetchSnapshot();
  const workbook = before.fetch.data.data;
  const version = before.fetch.data.v;
  const op = [];

  for (const edit of edits) {
    const sheet = workbook.sheets[edit.sheetId];
    const key = `${edit.rowId},${edit.colId}`;
    const cell = (sheet.cells || {})[key] || {};
    for (const field of edit.fields || ["0"]) {
      const part = {
        p: ["sheets", edit.sheetId, "cells", key, field],
        oi: edit.value,
      };
      if (Object.prototype.hasOwnProperty.call(cell, field)) part.od = cell[field];
      op.push(part);
    }
  }

  const ack = await new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => reject(new Error("op timeout")), 15000);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.begin) {
        ws.send(JSON.stringify({
          a: "op",
          c: msg.collectionID,
          d: msg.docID,
          v: version,
          op,
          src: msg.clientID,
          seq: 1,
        }));
      } else if (msg.a === "op") {
        clearTimeout(timer);
        ws.close();
        resolve(msg);
      }
    };
    ws.onerror = () => reject(new Error("op websocket error"));
  });

  if (ack.error) throw new Error(ack.error);
  return await fetchSnapshot();
}
```

Safe use checklist:

1. Use a fresh task tab; never mutate from an old offline/read-only tab.
2. Re-fetch immediately before writing and use that snapshot's `v`.
3. Require `scanned_data_rows == total_data_rows`, then compare the complete live and frozen eligible
   key sets in both directions.
4. Confirm names/headers from the same snapshot, not from stale screenshots.
5. Build paths with internal ids: `sheetId`, `rowId`, and `colId`.
6. For existing fields, include `od`; for blank fields, omit `od`.
7. Preserve style-only keys such as `"100"` by setting only value/link fields.
8. Verify from another full fresh snapshot and compare the entire eligible scope plus exact values.

Do not use this path for complex formatting, formulas, merged cells, protected ranges, or operations
whose POPO data schema is unknown. Fall back to grid actions or UI workflows when the edit is more
than simple cell value/link replacement.

## Screenshot And Diagnostic Artifacts

Diagnostics should prevent blind writes, not become the main navigation strategy. For POPO data tasks,
authoritative verification order is:

1. ShareDB snapshot re-fetch.
2. Grid action or clipboard copy-back TSV.
3. Screenshot evidence for visual state, formatting, UI fallback, or failure diagnosis.

Do not scroll repeatedly just to prove cell values that snapshot or copy-back can already prove. If a
target row is not visible, derive its visual row/column from `sheet.rows` and `sheet.cols`; only take
one targeted screenshot when a visual proof is needed for the user or for diagnosing a UI fallback.

Mandatory screenshot points:

- Initial orientation before UI fallback work.
- After focus/navigation tests when keyboard or mouse control is about to write.
- Immediately after any UI write, undo, protected/read-only toast, paste shift, or unexpected focus
  change.
- Final visual check only when formatting, row height, wrap, hyperlink styling, or user-facing
  presentation matters.

Skip screenshots when:

- A ShareDB write was verified by a fresh snapshot and no visual formatting changed.
- The only goal is row lookup or cell-value validation.
- Capturing the screenshot would require multiple scroll steps solely to reveal already-verified
  values.

### WebBridge Screenshot Path

Do not rely on a requested custom `path` for Kimi WebBridge screenshots. Some daemon/extension
versions ignore it and return a default temp path. Treat the returned `data.path` as the only source
of truth, then copy that file to a stable artifact folder if the image will be referenced later.

Recommended flow:

1. Call `screenshot` with minimal args such as `{"format":"png"}`.
2. Parse the JSON response.
3. Read `data.path`.
4. Confirm that file exists and has non-zero length.
5. Copy it to a stable task folder with a meaningful name.
6. Use the stable copy in reports; keep the temp path only as provenance.

If screenshot returns no usable path, stop UI work. Re-run the screenshot once if the browser was
still loading; otherwise report the diagnostic failure instead of continuing blind.

### Large WebBridge Responses

Large responses such as full ShareDB snapshots can exceed command-output limits. Do not print them to
stdout and hope the chat/tool output keeps everything. Write them to disk directly:

```powershell
curl.exe -s -X POST "http://127.0.0.1:10086/command" `
  -H "Content-Type: application/json" `
  --data-binary "@$requestPath" `
  -o "$responsePath"
```

Then parse or summarize the saved file locally. For snapshots, extract a compact row/column summary
before returning anything to the user.

### Helper Script

Use `scripts/webbridge_command.ps1` for Windows WebBridge calls in POPO tasks when available. It:

- writes request JSON as UTF-8 without BOM,
- writes WebBridge responses to a file with `curl.exe -o`,
- optionally copies returned screenshots from temp to a stable folder,
- prevents large snapshot/evaluate responses from being truncated in the tool output.

## Addressing Internals

There is no reliable O(1) name-box addressing exposed through WebBridge. The reliable path is:

1. Focus the office iframe: `document.querySelector('iframe').contentWindow.focus()`.
2. Send one harmless CDP key such as `ArrowLeft` or `ArrowUp`.
3. Verify the active cell moved. If it did not move, re-focus the iframe and retry once.
4. Use `Control+Home` to A1.
5. Arrow-step to the anchor and extend the selection.

This is why far ranges are slower and why row/column header operations should target headers/gutters.

## WebBridge Request Files On Windows

When posting JSON to a local WebBridge daemon from PowerShell, write request-body files as UTF-8
without BOM. PowerShell/.NET `Encoding.UTF8` can add a BOM on older runtimes, and WebBridge may fail
with:

```text
invalid character 'ï' looking for beginning of value
```

Use:

```powershell
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($tmp, $json, $enc)
curl.exe -s -X POST $url -H 'Content-Type: application/json' --data-binary "@$tmp"
```

Do not use shell quoting for large JSON payloads. Write the JSON file, send it with
`--data-binary`, and inspect the response body before continuing.

## Keyboard And Clipboard Fallback

Use this path when sheet actions are unavailable but WebBridge/CDP keyboard events can reach the
focused office iframe.

### Focus Test

1. Focus the iframe with top-frame JavaScript:
   `document.querySelector('iframe').contentWindow.focus()`.
2. Send `ArrowLeft` or `ArrowUp` via CDP `Input.dispatchKeyEvent`.
3. Screenshot or copy a small range to confirm active-cell movement before starting a long sequence.

### Modifier Keys

For range selection and clipboard shortcuts, prefer hold/release events over `modifiers` bitmasks.
POPO may ignore `modifiers: 8` for `Shift+ArrowDown` even when single arrow keys work.

Reliable pattern:

```text
Shift keyDown
ArrowDown keyDown/keyUp repeated N times
Shift keyUp
```

Use the same style for `Ctrl+C`, `Ctrl+V`, `Ctrl+Home`, and `Ctrl+Z`:

```text
Control keyDown
C keyDown/keyUp
Control keyUp
```

After any select + `Ctrl+C`, read the OS clipboard and verify it contains non-empty TSV with the
expected row/column shape. Do not proceed to matching or filling from an empty clipboard.

### Empty Clipboard After CDP Copy

If CDP can move the active cell or extend a visible selection but `Get-Clipboard` length is `0` after
CDP `Ctrl+C`, do not ask the user to copy yet. Run this escalation order:

1. Keep the current POPO selection visible.
2. Bring the exact browser window to the foreground with `computer-use` or the available app/window
   tool. On Windows, prefer the exact `MainWindowTitle`; partial titles can activate the wrong
   process or leave focus behind.
3. Re-focus the office iframe if WebBridge is still available.
4. Send native OS `Ctrl+C`.
5. Read `Get-Clipboard`; continue only when it contains non-empty TSV.
6. If native `Ctrl+C` also fails, retry once after clicking/focusing the grid canvas.
7. Ask the user to copy manually only after both CDP and OS-level copy paths fail.

Windows fallback when no richer OS-input tool is available:

```powershell
$title = 'exact browser MainWindowTitle here'
$ws = New-Object -ComObject WScript.Shell
$ws.AppActivate($title) | Out-Null
Start-Sleep -Milliseconds 300
$ws.SendKeys('^c')
Start-Sleep -Milliseconds 300
Get-Clipboard
```

For paste, reverse the flow:

```powershell
Set-Clipboard -Value $tsv
$ws = New-Object -ComObject WScript.Shell
$ws.SendKeys('^v')
```

Use this only when the correct browser window and POPO grid already have focus. If focus is unclear,
take a screenshot and re-focus the grid first.

Do not claim OS-level copy succeeded unless the agent first proves it created the selected range.
When the user is also working in the same POPO document, clipboard content may come from the user's
current selection.

### Screenshot Path

If a screenshot action accepts a custom `path` but returns a different default temp path, trust the
returned response path. Treat custom `path` as best-effort only unless the daemon version confirms
support for it.

## Format Inference Checklist

Before supplementing a sheet, inspect the local format contract from nearby completed rows:

- Header labels and target column meanings.
- Completed rows above the target region.
- Row height and wrap behavior.
- Hyperlink style: raw URL vs `HYPERLINK` formula.
- Borders and fill colors.
- Horizontal/vertical alignment.
- Dropdown/tag chips.
- Date, time, percentage, and number formats.
- Blank-cell policy.

Prefer the closest completed row in the same section. Ask only when two plausible styles would change
business meaning or cause destructive edits.

Header inference is the agent's responsibility. Do not ask the user to type exact header names when
headers are visible, copied, or inferable from common aliases. If the copied region lacks headers,
request a wider copy that includes the header row or inspect a screenshot/nearby completed rows.

## Name-Matched Bulk Fill

This is the preferred path for tasks like "fill fan count and homepage behind the corresponding
talent" where a source file and the live POPO sheet both contain names.

### Failure Pattern To Avoid

A slow or incorrect run usually comes from one of these mistakes:

- Using the source file's row numbers as POPO row numbers. POPO may have inserted rows, hidden rows,
  section rows, filters, or a different sort order.
- Clicking fixed coordinates row by row. Long links can wrap, row height can change, the viewport can
  auto-scroll, and canvas virtualization can shift the visible grid.
- Pasting after clipboard permission was denied. The action may no-op or paste stale clipboard data.
- Continuing after a protected-cell warning. Some cells or ranges may be editable while adjacent
  cells are protected.

### Robust Procedure

1. Parse the source file into records keyed by visible talent/name. For duplicate names, keep all
   candidates and require another column or user confirmation before writing.
2. Copy the complete requested POPO region from its header through its last requested row, including
   intervening rows, with the talent/name and target columns. Prove the copied row/key set covers the
   full requested scope before treating its TSV as authoritative. A viewport-only visible block is
   never sufficient.
3. Identify target columns by header text, aliases, screenshots, and nearby completed rows. For
   example, `粉丝量(w)`/`粉丝数`, `主页链接`/`主页`, `刊例价`/`报价`, and
   `平台价截图`/`报价截图` should be treated as aliases unless local evidence says otherwise.
4. Run `scripts/name_match_tsv.py` when both source data and copied POPO TSV are available as local
   files. The script builds the planned output block from the copied POPO rows:
   - matched rows get source values,
   - unmatched rows keep their current target values,
   - duplicate/conflict rows keep their current target values,
   - protected rows keep their current target values and are skipped.
5. 🔴 CHECKPOINT: inspect `match_report.tsv` before writing. Do not paste when duplicate names,
   unexpected source-only names, or missing source rows would change business meaning.
6. Paste a full rectangular TSV only when that rectangle covers the complete requested scope; keep
   intervening non-target rows unchanged in the plan. If full coverage cannot be represented safely,
   stop instead of writing a visible subset.
7. Immediately copy the complete requested target range back and compare every requested key/value
   with the plan, not only the pasted subset.
   🛑 STOP if any row differs, undo if appropriate, and diagnose before another write.

### Clipboard Permission Preflight

Before UI paste fallback, verify clipboard permission with a harmless sentinel:

1. Run a browser-side clipboard write such as `__popo_clipboard_probe__`.
2. Read it back or paste it into a non-sheet scratch target if direct read is unavailable.
3. If the browser denies clipboard access, ask the user to allow clipboard permission and do not
   start data-changing paste operations yet.

### Match Report Minimum

Before writing, produce or internally inspect a compact report with:

- total POPO rows copied,
- total source records,
- exact/normalized matches,
- duplicate conflicts,
- POPO names missing from source,
- source names not present in POPO,
- planned paste blocks and skipped protected/read-only blocks.

For high-volume fills, save this report as a temporary artifact or print it in the tool log so the
operator can catch obvious mismatches before paste.

## Menu Evidence From Live POPO Sheet

### Row Header

Validated row-height path:

1. Select rows from left row-number gutter.
2. Right-click selected gutter.
3. Choose `设置行高`.
4. Enter height.
5. Confirm and screenshot-check.

Live validation: rows 2-11 were set to `60`; row 1 stayed unchanged.

Failure signatures:

- No `设置行高`: clicked normal cells instead of row gutter.
- Height still wrong: selection missed rows or wrap state affected display.
- Writes no-op: likely read-only duplicate tab.

### Column Header

Observed column-header right-click menu:

- `复制`
- `剪切`
- `全部粘贴`
- `选择性粘贴`
- `清除`
- `隐藏列`
- `向左插入 <n> 列`
- `向右插入 <n> 列`
- `删除第<n>列`
- `设置列宽`

### Ordinary Cell Context Menu

Observed on a normal non-link cell:

- `复制`
- `复制为图片`
- `复制定位链接`
- `剪切`
- `全部粘贴`
- `选择性粘贴`
- `清除`
- `富文本编辑`
- `隐藏行`
- `隐藏列`
- `插入单元格`
- `删除单元格`
- `添加评论`

Do not use ordinary-cell clicks to select hyperlink cells unless explicitly opening a link.

### Border Dropdown

Observed toolbar border dropdown includes:

- Multiple border-position icons for side/inside/all/none style operations.
- Border line style dropdown.
- Border color dropdown.

Use screenshots to distinguish explicit borders from default gridlines.

### Quick Fill

Observed toolbar quick-fill dropdown:

- `智能填充`
- `序列填充`

These can alter data; require explicit user intent and before/after verification.

### Sheet Tab Menu

Observed bottom sheet-tab context menu:

- `插入`
- `删除`
- `隐藏工作表`
- `创建副本`
- `创建为独立表格`

Delete/export/share-style operations require explicit confirmation.

## Hyperlinks

Grid actions never click data cells, so they do not open links. To check/open a link only when the
user asks:

1. Read link text/formula first.
2. Select the link cell.
3. Click once intentionally.
4. Inspect opened page/tab.
5. Return to the sheet tab.

## In-Place Edit Mode

Overwrite with `sheet_fill` when possible. To edit inside an existing cell:

1. Select cell.
2. Double-click to enter edit mode.
3. `Control+A`.
4. Paste/type replacement.
5. `Enter` to commit or `Escape` to cancel.

## Pixel Click Conversion

If using screenshot coordinates:

1. Get `innerWidth` from `evaluate`.
2. Compute `scale = innerWidth / screenshotWidth`.
3. Convert screenshot coordinates to CSS coordinates.

Prefer keyboard/header selection over blind pixel clicks.

## Side Effects And Limits

- Clipboard-backed actions temporarily modify the OS clipboard; robust wrappers should restore it.
- Values containing tab/newline can split TSV blocks and misalign paste.
- Visual formatting needs screenshot verification; `sheet_read` verifies only content.
- A keyboard sequence is not proven until select + `Ctrl+C` produces a non-empty TSV from the OS
  clipboard.
- Empty clipboard after CDP `Ctrl+C` is a clipboard-delivery failure, not proof that the selected
  POPO range is unusable. Try OS-level copy before involving the user.

## Bundled Script Contract

Use `scripts/name_match_tsv.py` to remove repeated ad hoc matching logic.

Required inputs:

- `--source`: Markdown table, TSV, or CSV source file.
- `--popo`: TSV copied from POPO with one header row.
- `--target-cols`: comma-separated POPO target column headers, only when exact targets are known.
- `--out-paste`: paste TSV path.
- `--out-report`: match report TSV path.

Optional inputs:

- `--name-col`: shared name column header.
- `--source-name-col` and `--popo-name-col`: separate name headers when the two files differ.
- `--preset talent-basic`: auto-detect common talent columns such as name, fans, homepage, rate, and
  rate screenshot. Use this before asking the user for header names.
- `--source-map`: comma-separated `POPO_COL=SOURCE_COL` overrides when target column names differ.

Outputs:

- `paste.tsv`: target columns only, one row per copied POPO data row, no header row. Select the same
  target columns in POPO data rows before pasting.
- `match_report.tsv`: row-level statuses: `matched`, `missing_source`, `duplicate_source`,
  `duplicate_popo`, and `source_only`.

🛑 STOP conditions:

- Any `duplicate_source` or `duplicate_popo` row affects a row the user expects to fill.
- `missing_source` appears for names that should be present.
- `source_only` contains names the user expected to appear in the POPO section.
- The copied POPO TSV does not include the target headers.

## Engine API Investigation

Direct engine access is currently blocked through Kimi WebBridge, but keyboard control may still work:

- The real grid engine runs in the `office.netease.com` child iframe.
- Cross-frame JS access throws `SecurityError`.
- WebBridge `evaluate` runs in the top frame only.
- CDP target/frame access may be restricted for DOM inspection, but `Input.dispatchKeyEvent` can
  control the focused iframe in verified setups.
- Loading the office iframe URL standalone renders blank.

Production value/link path is ShareDB snapshot plus simple write with structural readback. Grid
actions, iframe focus, and clipboard TSV are bounded fallbacks for formatting or unavailable
structured access.
