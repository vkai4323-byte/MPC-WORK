# Portable document providers

Read this reference whenever `documents.mode` is `create` or `update`.

## Resolve a provider

Run from the skill directory:

```powershell
python scripts/document_provider.py --format json
```

For a manifest job, repeat `--required-capability <name>` for every capability emitted by `compose_chain.py`. Treat the JSON `status` as authoritative. Exit code `0` means ready or explicitly disabled, `4` means bundled credentials are missing, `3` means a legacy/connector/capability preflight is still required, and `2` means invalid or unsupported configuration.

Use Python 3.10 or newer. If `python` is a Windows Store shim, use the workspace-bundled Python.

Resolution order is an explicit `--command`, an explicitly selected config, `FEISHU_DOC_CLI`, per-user local config, a compatible command on `PATH`, then the bundled `scripts/feishu_doc.py`. Relative commands in a config resolve from that config file's directory. The resolver returns only a logical provider reference and never exposes its path or argv. The script cannot inspect an Agent's connector catalog; when it returns `needs_credentials` or `unavailable`, capability-check an authenticated document connector exposed to the Agent before asking the user to configure app credentials locally.

Accept a provider only when it supports the required subset of: resolve, copy, raw/read, block read, grouped replacement with dry-run, permission read/update, exact read-back, and structural signature. Do not substitute browser editing for a missing provider.

## Keep local overrides outside the skill

Set `RESEARCH_SHEET_PIPELINE_CONFIG` to a JSON file, or create one of:

- `$CODEX_HOME/research-sheet-pipeline.local.json`
- `~/.codex/research-sheet-pipeline.local.json`
- `~/.config/research-sheet-pipeline/config.json`

Example:

```json
{
  "document": {
    "provider": "feishu-cli",
    "command": "feishu-doc"
  }
}
```

The legacy manifest field `tools.feishu_cli` remains accepted as an explicit pinned command. Pass it to the resolver with `--command ... --pin` without echoing it; a missing pinned command fails closed. Do not put machine-specific paths in the distributed job template.

An external legacy CLI may keep its existing private credential mechanism, including a keychain or authenticated session. It is always `legacy_unverified`: the resolver does not assume its credential names or capabilities. Treat only capabilities established by an explicit Agent-side declaration or read-only preflight as available, and never emit paths or credential values.

## Configure Feishu credentials safely

The bundled adapter reads only `FEISHU_APP_ID` and `FEISHU_APP_SECRET` from the process environment or, in order, from:

1. the file named by `FEISHU_ENV_FILE` or `--env-file`;
2. `$CODEX_HOME/secrets/research-sheet-pipeline/feishu.env`;
3. `~/.config/research-sheet-pipeline/feishu.env`.

The bundled adapter uses tenant-app identity. If the task requires a user's personal authorization or data scope, select an authenticated Agent connector instead of reusing tenant credentials.

The app must also have the required Wiki, Docx, Drive, and permission scopes, be published/approved as required by the tenant, and have access to the exact source and destination. `doctor --auth` validates token issuance only; prove resource access with one read-only resolve/read/permission preflight before mutation.

Use:

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=<set-locally>
```

Never ask the user to paste `FEISHU_APP_SECRET` into chat. Never print its value, store it in a manifest/run artifact, or commit a credential file. If credentials are missing, instruct the user to configure them locally and wait for confirmation.

Run a non-secret readiness check:

```powershell
python scripts/feishu_doc.py doctor
```

With user authorization, add `--auth` for one read-only token validation. The report exposes only presence, source class, status, and capabilities.

Run a resolved provider without revealing its locator:

```powershell
python scripts/document_provider.py --run -- resolve "https://example.feishu.cn/wiki/TOKEN"
```

An external legacy CLI is reported as `legacy_unverified`; perform one read-only resolve/raw preflight before mutation. The bundled adapter supports `FEISHU_REGION=feishu|lark`. Its grouped text replacement uses one idempotent Feishu batch update for at most 200 affected blocks, then verifies exact content and an inline-style-aware structural signature. If it reports `apply_status: unknown` or failed read-back, inspect the target first and resume from verification; never blindly replay the batch. Public-anyone editing additionally requires `--confirm-file-token` matching the exact target and always performs a permission read-back.

## Preserve completed work

If no provider becomes ready within the tool-preflight budget, stop only the `document` stage. Keep normalized records and upstream evidence, report the missing provider or credentials, and resume from `document` after configuration. Never rerun sheet acquisition or external research because document setup failed.
