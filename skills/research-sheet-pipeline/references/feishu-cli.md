# Feishu online documents through the official CLI

Use this reference for every Feishu/Lark online-document task, including Wiki links whose
underlying object type is not known yet.

## Hard routing rule

1. Resolve `scripts/document_provider.py` with every capability the job needs.
2. The primary provider is the official `lark-cli`.
3. Read the version-matched embedded skill before using a domain:
   - global auth and safety: `lark-cli skills read lark-shared`
   - documents: `lark-cli skills read lark-doc`
   - spreadsheets: `lark-cli skills read lark-sheets`
   - Drive/files/comments/permissions: `lark-cli skills read lark-drive`
   - Wiki spaces/nodes: `lark-cli skills read lark-wiki`
   - Base/bitable: `lark-cli skills read lark-base`
   - Slides: `lark-cli skills read lark-slides`
4. Read every operation-specific reference named by the embedded skill before acting.
5. Prefer `+` shortcuts, then typed API commands, then `lark-cli api` as the final CLI
   escape hatch. The bundled Python adapter is a compatibility fallback only.
6. Browser automation is never a fallback for Feishu document access or editing.

## Domain routing

| Target/action | CLI domain |
|---|---|
| Inspect URL, search, copy, move, import/export, comments, collaborators, public permissions | `drive` |
| Docx read/create/update, blocks, media, history | `docs` |
| Spreadsheet values, formulas, styles, validation, filters, charts, pivots, structure, import/export, history | `sheets` |
| Wiki space/node/member read and management | `wiki` |
| Base/bitable tables, fields, records, views, dashboards, workflows, permissions | `base` |
| Presentations/slides | `slides` |
| Native Drive Markdown files | `markdown` |

A `/wiki/<token>` URL is only a node locator. Resolve it first with `drive +inspect`,
`wiki +node-get`, the typed `wiki spaces get_node`, or the raw Wiki API. Route the returned
`obj_type` and `obj_token` to the owning domain.

## Capability and permission checks

`doctor` proves the CLI configuration and at least one identity are usable; it does not prove
that the selected identity has every resource scope or document ACL. Before mutation:

- resolve the provider with all required capabilities;
- run a read-only command against the exact target;
- keep `bot` and `user` identity distinct;
- treat missing scope and missing document ACL as separate failures;
- do not switch identity silently;
- never print app secrets or access tokens.

When a shortcut is unavailable because the app lacks a newer scope, use a documented typed or
raw OpenAPI route through `lark-cli` only if the existing identity is authorized for that exact
endpoint. Otherwise stop that stage and report the missing scope.

## Mutation protocol

- Read structure and exact targets first.
- Record a revision/history anchor when the domain exposes one.
- Use `--dry-run` when supported.
- Prefer an atomic batch shortcut for multiple related edits.
- High-risk commands that return exit code 10 require explicit user confirmation before adding
  `--yes`.
- After writing, use the owning domain's read command to verify every changed range/object.
- For Sheets, also inspect changesets/formula errors when relevant.
- Never treat `ok: true` as sufficient verification.

## Provider invocation

Run the official CLI through the resolver so its executable location and credentials stay private:

```powershell
python scripts/document_provider.py --required-capability spreadsheet.read
python scripts/document_provider.py --run -- sheets +workbook-info --url "<url>"
python scripts/document_provider.py --run -- docs +fetch --doc "<url>"
python scripts/document_provider.py --run -- drive +inspect --url "<url>"
```

The official CLI supports schema discovery for typed endpoints and a raw escape hatch:

```powershell
python scripts/document_provider.py --run -- schema "<service.resource.method>"
python scripts/document_provider.py --run -- api GET "/open-apis/..."
```

Pass large JSON through stdin. Keep temporary payloads outside the user project. Do not reveal
the provider command path, profile files, credential files, app secret, or tokens.
