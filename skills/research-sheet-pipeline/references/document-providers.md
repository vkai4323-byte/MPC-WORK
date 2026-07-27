# Portable document providers

Read this reference whenever `documents.mode` is `create` or `update`.

## Contents

- Resolve a provider
- Keep local overrides outside the skill
- Configure Feishu credentials safely
- One writer and unknown outcomes
- Audience-facing content
- Preserve completed work

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

An external legacy CLI is reported as `legacy_unverified`; perform one read-only resolve/raw preflight before mutation. The bundled adapter supports `FEISHU_REGION=feishu|lark`. Its grouped text replacement uses one idempotent Feishu batch update for at most 200 affected blocks, then verifies exact content and an inline-style-aware structural signature. Public-anyone editing additionally requires `--confirm-file-token` matching the exact target and always performs a permission read-back.

## One writer and unknown outcomes

Before mutation, assign one writer owner and one idempotency key to the destination. No other process, task turn, or fallback may mutate that destination until the owner reaches `verified`, `blocked`, or a freshly observed safe-to-retry state.

If a command times out, exits without a conclusive read-back, or reports `apply_status: unknown`:

1. retain `running` while the original process is live; otherwise record `unknown`; both require `writer_count=1`, writer owner, idempotency key, process state, and `actual_state_checked=false`;
2. retain the writer lock;
3. wait for or terminate the original process so it cannot continue in the background;
4. resolve/read the target from fresh state;
5. compare intended content, structural signature, and permission;
6. release the writer and mark verified only when the intended post-state is already present, with `process_state=ended`, `actual_state_checked=true`, and a non-empty readback proof;
7. otherwise compute only the remaining delta and retry once with fresh preconditions/idempotency.

Never start a recovery writer while the original process or outcome is unresolved. A timeout is not proof that no mutation occurred.

## Audience-facing content

For `documents.audience=external`, require `documents.content_policy=deliverable_only`. Before apply and again after read-back, reject:

- internal reasoning or strategy notes;
- task instructions, constraints, or acceptance criteria;
- debugging/recovery commentary;
- placeholders that describe what content should exist;
- copied prompt text or change-plan language.

Keep those details only in the manifest/checkpoint/change plan. The document itself must contain only audience-facing material.

## Preserve completed work

If no provider becomes ready within the tool-preflight budget, stop only the `document` stage. Keep normalized records and upstream evidence, report the missing provider or credentials, and resume from `document` after configuration. Never rerun sheet acquisition or external research because document setup failed.
