---
name: douyin-xingtu
description: Retrieve authenticated, read-only Douyin Xingtu creator search, pricing, recent-video, ranking, content, and task-report data. Screen similar creators from the user's actual campaign purpose with non-negotiable eligibility gates, content review, and transparent tiers. Prefer the reusable direct HTTP client with a Windows-DPAPI encrypted session; use Kimi WebBridge only for initial request capture, expired-session recovery, CAPTCHA, or interface repair. Use for 星图达人检索、相似达人筛选、COSER投放、达人报价、近期视频播放量、榜单、内容灵感、任务报告、作品ID与达人身份核验。
---

# Douyin Xingtu

Prefer the reusable direct client. Do not drive the browser per record.

## Direct client

Run from this skill directory:

```powershell
python scripts/xingtu_direct.py status
python scripts/xingtu_direct.py self-test
python scripts/xingtu_direct.py search --keyword "搞笑剧情" --search-type content --pages 3 --output results.json
python scripts/xingtu_direct.py search --keyword "虎纹章鱼" --search-type nickname --output target.json
python scripts/xingtu_direct.py items --item-id 7666679758713954118 --output items.json
```

The direct client:

- calls only allowlisted Xingtu read endpoints;
- decrypts the session only in memory;
- retries bounded `429/502/503/504` responses;
- returns `auth_required:xingtu_session_expired` on explicit session expiry;
- never logs cookies, request headers, tokens, or advertiser account identifiers.

### Initialize or refresh the session

Only when the user explicitly authorizes a reusable direct session:

1. Ask the user to open the authenticated creator market once.
2. Capture `POST /gw/api/gsearch/search_for_author_square` with **Copy as cURL (bash)**.
3. Pipe the cURL text to:

```powershell
Get-Clipboard -Raw | python scripts/xingtu_direct.py import-curl
```

`import-curl` stores the session and search template at
`%LOCALAPPDATA%\Codex\XingtuDirect\session.dpapi`, encrypted with Windows DPAPI for the current
Windows user. Never store plaintext cURL, cookies, or headers. Do not delete the encrypted session
after a normal read; retain it for reuse until the user asks to remove it or the session expires.

Run `self-test` after import. Do not request another capture while `self-test` returns `ready`.

## WebBridge fallback

Use the bundled browser client only when:

- no direct session exists and the user declines encrypted persistence;
- direct `self-test` returns `auth_required`;
- CAPTCHA or login interaction is required;
- Xingtu changed a request schema and the direct client must be repaired.

```powershell
python scripts/xingtu_batch.py self-test
python scripts/xingtu_batch.py items --input items.json --output item-results.json
python scripts/xingtu_batch.py published-items --input published.json --output verified-results.json
python scripts/xingtu_batch.py authors --input authors.json --output author-results.json
python scripts/xingtu_batch.py ranking --output ranking.json
python scripts/xingtu_batch.py task-reports --output task-reports.json
```

Never use Computer Use or UI clicking for production batch retrieval.

## Identity and metrics

1. Prefer exact `item_id -> author_id/core_user_id -> star_id -> exact display name`.
2. Do not select the first nickname result automatically.
3. Preserve source keys separately from normalized matching values.
4. Stop affected records on `ambiguous`, `identity_conflict`, `metric_conflict`, `not_found`,
   `auth_required`, or `page_not_ready`.
5. Prefer item-detail `stats.watch_cnt` for current plays.
6. Preserve creator-search `last_10_items.vv` as a separate cached observation when it differs.
7. Record `observed_at`, endpoint, and source field for writable metrics.
8. Treat signed cover/share URLs as ephemeral evidence.

Read [references/output-contract.md](references/output-contract.md) before sheet writeback.
Read [references/xingtu-observed-api.md](references/xingtu-observed-api.md) when extending or
repairing the direct client.

## Similar-creator research

Read [references/multi-pass-retrieval.md](references/multi-pass-retrieval.md) before every
similar-creator or campaign-fit search. Read
[references/similar-creator-screening.md](references/similar-creator-screening.md) additionally
when the hard gate or final review needs domain-specific interpretation.

### Purpose before similarity

Identify why the user chose the reference creator. Convert that campaign purpose into a
non-negotiable eligibility gate before searching.

- If the purpose is producing COS content, `active real-person COSER` is the gate.
- Beauty, humor, abstraction, acting, price, and reach are secondary ranking dimensions.
- Never replace a failed gate with a high secondary score.
- If too few candidates pass, expand recall. Do not relax the gate or fill the quota with adjacent
  non-COS accounts.

### Evidence hierarchy

Use evidence in this order:

1. recent item covers and titles;
2. representative items and recent publishing consistency;
3. creator labels and query hits as recall signals only.

Keyword counts are title-match counts, not proof that a creator lacks or has a capability. Label
them explicitly in deliverables, for example `近10条标题COS命中`.

### Candidate workflow

1. Write required, preferred, and excluded traits.
2. Inspect the reference creator before inventing search terms.
3. Run orthogonal retrieval passes: broad theme, identity/format, behavior/mechanism, and
   external-seed recovery when useful.
4. Merge every pass and deduplicate by `core_user_id`; retain query/pass provenance.
5. Apply the hard gate before similarity scoring.
6. Fetch recent-item detail only for the review pool, then inspect covers/titles for every
   finalist.
7. Exclude conflicting account types discovered during review.
8. Tier only the passing set. For COS campaigns, use:
   - `核心相似`: active COS plus humor, contrast, acting, or narrative performance;
   - `稳定COS`: high recent COS consistency but weaker performance/abstract traits;
   - `可投COS`: real-person COS is established, but role fit needs a second-round sample review.
9. Deliver requested metrics with blanks and status notes for unavailable fields. Never invent
   prices or plays.

Do not fetch expensive item detail for the entire recall pool. Use search summaries to narrow the
pool first, then enrich finalists.

COS example commands after direct-session readiness:

```powershell
python scripts/xingtu_direct.py search --keyword "coser" --search-type content --pages 10 --output coser.json
python scripts/xingtu_direct.py search --keyword "女coser" --search-type content --pages 10 --output female-coser.json
python scripts/xingtu_direct.py search --keyword "二次元cos" --search-type content --pages 10 --output acg-cos.json
python scripts/xingtu_prepare_review.py --input candidates.json --output review.json --creators 100 --items-per-creator 2 --personal-only
python scripts/xingtu_download_review_covers.py --input review.json --output-dir covers --creators 100
```

## Hard safety boundary

Allowed:

- authenticated Xingtu creator-market reads;
- allowlisted read-only GET requests;
- creator/content search POST requests;
- local JSON and spreadsheet outputs;
- user-authorized DPAPI-encrypted session persistence.

Never automate:

- orders, task publishing, or creator-list mutation;
- finance, balance, settlement, recharge, or refunds;
- project state changes, messages, or audience pushes;
- permission, qualification, account, or collaboration changes.
