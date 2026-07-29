# Similar-creator screening

Use this as the COS campaign example after applying the general workflow in
[multi-pass-retrieval.md](multi-pass-retrieval.md).

## 1. Start from the buying purpose

Ask: **What must the creator be able to produce for this campaign?**

Turn the answer into a hard gate. Do not begin with platform labels or the reference creator's
broad category.

Example:

| Layer | COS campaign definition |
|---|---|
| Required | Real-person creator; actively publishes COS; individual creator account |
| Preferred | Humor/abstract behavior, contrast, acting, narrative performance, beauty |
| Commercial | Price, followers, recent plays, role fit, delivery reliability |
| Excluded | Non-COS beauty, game commentary, anime reposts, makeup tutorials, shops, studios, official accounts |

The required layer is boolean. Preferred and commercial layers rank only the creators who pass.

## 2. Apply the multi-pass workflow to COS

Map the general passes to COS-specific searches:

- broad semantic: `二次元`, the target IP, role or character;
- identity/format: `coser`, `女coser`, `cosplay`, `cos`, `二次元cos`;
- behavior/mechanism: `coser整活`, `coser反差`, `coser搞笑`;
- external recovery: known COSER names, followed by exact Xingtu verification.

Use enough pages to produce a pool larger than the requested output. Deduplicate by
`core_user_id`, not nickname.

When the passing pool is too small:

1. add more COS-specific queries;
2. increase pages;
3. add role/IP variants;
4. inspect previously missed individual creators.

Never fill the quota with non-COS accounts.

## 3. Separate recall signals from proof

Platform labels, query hits, and title keyword counts are useful for recall and sorting, but they
do not prove campaign fit.

- A `二次元` label can describe game commentary, animation, shops, or real COS.
- A title without the word `cos` can still show a clear costume performance.
- A nickname containing `coser` can belong to a store, studio, compilation, or inactive account.

Name count columns precisely:

- `近10条标题COS命中`
- `近10条标题整活命中`

Do not name them `COS作品数` unless every item was visually classified.

## 4. Review the content, not only metadata

For every finalist, inspect at least two recent item covers and titles. Use more when the account
is ambiguous.

Confirm:

- the creator is a real individual, not a service account;
- recent publishing still contains COS;
- the person is the content subject, not merely reporting on COS;
- the account's current format matches the campaign;
- performance/abstract traits are visible when used for a higher tier.

Reject obvious conflicts:

- COS shops, costume/wig sellers, makeup-only tutorials, studios, selfie venues;
- official events, media accounts, compilations, reposts;
- game guides/commentary using character images;
- animation or AI-character accounts;
- unrelated beauty/lifestyle accounts.

## 5. Tier only eligible creators

For a COS campaign:

- **核心相似**: active COS plus recent or representative evidence of humor, contrast, acting, or
  narrative performance.
- **稳定COS**: consistently publishes COS with reliable role presentation; humor/abstract traits
  are weaker.
- **可投COS**: real-person COS is established, but frequency, performance style, or role match
  requires a second-round sample review.

Do not create an “adjacent non-COS” tier inside a deliverable whose purpose is hiring COSERs.

## 6. Deliver transparent metrics

Include:

- follower count;
- 0–20s quote;
- 30-day median plays when available;
- at least two recent item play counts, titles, and URLs;
- observation time and source;
- a status for missing price or item detail.

Prefer current item-detail plays over cached search-result plays. Leave unavailable fields blank
and explain the status.

## 7. Failure patterns to prevent

| Failure | Prevention |
|---|---|
| Treating “similar” as broad label overlap | Anchor the hard gate to campaign purpose |
| Ranking abstract beauties who cannot produce COS | Apply the COS gate before scoring |
| Filling to 50 by weakening the requirement | Expand COS-specific recall instead |
| Treating title counts as ground truth | Inspect covers/titles and label counts as keyword hits |
| Returning stores, studios, or media accounts | Use individual-account exclusions and visual review |
| Reusing browser automation for every record | Keep the DPAPI direct session and batch direct reads |
