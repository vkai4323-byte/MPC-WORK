# Multi-pass creator retrieval

Use this framework for any similar-creator search. It is not specific to COS, beauty, comedy, or
any single content category.

## Core model

Treat retrieval as a sequence of different evidence passes, not repeated synonyms for one query.

Each pass must answer a different question:

1. **Reference reconnaissance** — what does the target actually publish?
2. **Broad semantic recall** — who appears in the same broad subject space?
3. **Identity/format recall** — who is the required kind of creator or account?
4. **Behavior/mechanism recall** — who creates attention in the same way?
5. **External-seed recovery** — which obvious candidates did Xingtu keyword search miss or rename?
6. **Evidence enrichment** — do recent works confirm the required capability?

Not every task needs every pass. Use only the passes that add independent recall or validation.

## Pass 0: reference reconnaissance

Search the target by exact nickname and verify identity. Inspect:

- labels and tags;
- representative items;
- recent 10-item titles and cached plays;
- at least several recent covers;
- content format, subject, performance mechanism, audience promise, and account type.

Write a compact target model:

```text
campaign purpose:
required capability:
preferred traits:
excluded formats:
commercial metrics:
```

Do not derive the search plan from platform labels alone.

## Pass 1: broad semantic recall

Search the high-level themes visible in the target's content. Use this pass to discover vocabulary,
not to finalize creators.

Examples:

- food: `探店`, `美食测评`, `地方小吃`;
- knowledge: `科普`, `冷知识`, `实验`;
- beauty: `变装`, `妆造`, `颜值`;
- comedy: `搞笑剧情`, `抽象`, `整活`.

Expect high noise. Preserve the query that found each creator.

## Pass 2: identity and format recall

Search terms that represent the non-negotiable creator type or production format.

Examples:

- `coser`, `女coser`, `cosplay`;
- `探店达人`, `真人测评`;
- `剧情博主`, `夫妻账号`;
- `手工达人`, `真人出镜`;
- the required game, sport, profession, region, language, or audience identity.

This pass protects the hard gate from being overwhelmed by broad-topic matches.

## Pass 3: behavior and mechanism recall

Search for how the content works rather than only what it is about.

Possible mechanisms:

- humor, absurdity, contrast, social embarrassment;
- transformation, reveal, challenge, test, comparison;
- acting, narrative reversal, recurring character;
- expertise, tutorial, teardown, ranking;
- emotional companionship, aspiration, controversy, spectacle.

Combine mechanism terms with the identity/format when the broad term is noisy:

```text
<creator type> + <mechanism>
<content format> + <mechanism>
<IP or subject> + <mechanism>
```

Example: prefer `coser整活` over a standalone `整活` query when COS is required.

## Pass 4: external-seed recovery

Use public search or known lists only to discover names when Xingtu recall is incomplete. Then
resolve every seed back inside Xingtu.

For each external seed:

1. search exact nickname;
2. inspect multiple candidates when names collide;
3. allow renamed-account recovery using recent items or stable IDs;
4. keep only Xingtu-verified identities;
5. do not use public-page metrics in place of Xingtu metrics.

Use `scripts/xingtu_lookup_names.py` for exact-name recovery when appropriate.

## Pass 5: merge and deduplicate

Union all passes by stable identity:

```text
core_user_id -> star_id -> exact current nickname
```

Retain:

- `_query_hits`: exact search terms that returned the creator;
- `_source_passes`: broad, identity, mechanism, or external;
- the strongest search record and all useful recent-item summaries.

Never deduplicate by nickname alone.

## Pass 6: cheap screening before detail fetch

Use search-result data to remove obvious conflicts:

- wrong gender or account type when required;
- official, media, store, service, studio, compilation, or repost accounts;
- conflicting content labels;
- no recent evidence of the required format;
- inactive or clearly irrelevant accounts.

Use titles and labels as screening signals, not final proof.

Aim for a review pool larger than the final request:

- requested `N`;
- initial unique recall: roughly `3N–8N` when search supply allows;
- enriched review pool: roughly `1.2N–2.5N`;
- final set: `N` eligible creators.

These are planning ranges, not quotas. Do not weaken eligibility to hit them.

## Pass 7: evidence enrichment

Fetch item detail for the reduced review pool:

- at least two recent items per creator;
- current play count;
- title and canonical/share URL;
- cover for visual inspection;
- more items when identity or format is ambiguous.

Use `scripts/xingtu_prepare_review.py` and
`scripts/xingtu_download_review_covers.py` for this stage.

Inspect the evidence to confirm:

- the required capability is current, not historical;
- the creator is the content subject;
- the account format is compatible with the campaign;
- preferred mechanisms are genuinely present;
- exclusions were not hidden by labels or nicknames.

## Pass 8: scoring and tiering

Apply gates and scores in this order:

1. **eligibility gate** — boolean required capability;
2. **content-fit score** — preferred traits and mechanism similarity;
3. **commercial score** — followers, price, recent plays, efficiency;
4. **confidence** — strength and recency of evidence.

Never let a high commercial or behavioral score compensate for a failed eligibility gate.

Name automated fields precisely:

- use `标题关键词命中数`, not `作品数`, unless every item was classified;
- use `搜索缓存播放`, not `当前播放`, when item detail was not fetched;
- distinguish automated score from manual review tier.

## Pass 9: stopping rules

Stop expanding recall when:

- the eligible review pool safely covers the requested output;
- two new orthogonal passes add few or no new eligible creators;
- remaining results are dominated by known exclusion types;
- identity or metric uncertainty would make additional rows unsafe.

If the eligible set is still short, report the gap. Do not silently relax the gate.

## Reusable provenance record

Keep a compact record per creator:

```json
{
  "core_user_id": "",
  "star_id": "",
  "nick_name": "",
  "query_hits": [],
  "source_passes": [],
  "required_gate": {"passed": false, "evidence": []},
  "preferred_evidence": [],
  "exclusion_hits": [],
  "recent_items": [],
  "metrics": {},
  "review_status": "unreviewed"
}
```

This makes the result auditable and allows new passes to merge without restarting.
