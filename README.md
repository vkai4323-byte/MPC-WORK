# MPC-WORK

Complete creator-research and spreadsheet-writeback framework.

## Three composable Skills

- [`research-sheet-pipeline`](skills/research-sheet-pipeline/): freezes exact sheet scope, routes
  platform research, coordinates Feishu documents, and performs guarded field-level writeback.
- [`popo-sheet`](skills/popo-sheet/): reads and writes POPO sheets through ShareDB with exact internal
  IDs, `init`-gated requests, staged timeout diagnostics, and full structural verification.
- [`douyin-xingtu`](skills/douyin-xingtu/): authenticated, read-only Xingtu retrieval for Douyin
  publications, creator identity, metrics, pricing, rankings, content, and task reports.

## Included integrations

- Feishu skill/provider layer: `feishu-cli.md`, `document-providers.md`, `document_provider.py`, and
  the credential-free compatibility adapter `feishu_doc.py`.
- Bilibili API layer: `bilibili_batch.py` with the dependencies declared in
  `skills/research-sheet-pipeline/requirements.txt`.

Credentials, browser sessions, `.deps`, raw workbooks, checkpoints, and production results are not
included.

See [HANDOFF.md](HANDOFF.md) for architecture, validation commands, field-level blocking rules, and
operating constraints.

Use Python 3.10+ and install Bilibili dependencies with:

```bash
pip install -r skills/research-sheet-pipeline/requirements.txt
```
