# MPC-WORK

Reusable workflow skills and supporting resources.

## Skills

- [`research-sheet-pipeline`](skills/research-sheet-pipeline/): API-first research, normalization, portable Feishu document providers, and spreadsheet writeback with POPO and authenticated-browser fallbacks.
- [`douyin-xingtu`](skills/douyin-xingtu/): authenticated, read-only Xingtu retrieval for Douyin publications, creator identity, metrics, pricing, rankings, content, and task reports.

See [HANDOFF.md](HANDOFF.md) for architecture, validation, operating constraints, and continuation guidance.

The skill bundles a credential-free Feishu Python adapter and auto-detects compatible per-user CLIs. Feishu App credentials remain outside the repository and are never stored in job manifests.

Use Python 3.10+ and install its Bilibili dependencies with:

```bash
pip install -r skills/research-sheet-pipeline/requirements.txt
```
