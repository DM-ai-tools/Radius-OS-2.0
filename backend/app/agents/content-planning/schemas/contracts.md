# Content planning contracts

Join key is `url_n`: lowercase path, no query/fragment, trailing slash stripped except `/`.

Radius OS CDP field aliases are accepted (listed under each pack). Prefer the canonical names when emitting JSON.

## Catalogs

**priority_tier:** `quick_win` | `big_bet` | `fill_in` | `avoid`

Aliases from content-strategy: `Quick win` → `quick_win`, `Big bet` → `big_bet`, `Fill-in` → `fill_in`, `Avoid` → `avoid`.

**page_type:** `home` | `hub` | `spoke` | `pillar` | `cluster` | `article` | `blog` | `service` | `subservice` | `product` | `location` | `commercial` | `supporting` | `utility` | `landing` | `faq` | `comparison` | `guide` | `listicle` | `tool`

Aliases: `type` on `target_url_tree` rows; legacy `sub_service` normalizes to `subservice`.

**disposition (7-value):** `KEEP` | `REFRESH` | `OPTIMISE` | `RETITLE` | `CONSOLIDATE` | `NOINDEX` | `DELETE_CANDIDATE`

Aliases (lowercase from content-audit inventory): `keep`, `refresh`, `optimise`/`optimize`, `retitle`, `consolidate`, `noindex`, `delete_candidate`/`retire`.

**coarse action:** `create` | `refresh` | `retire` | `no_action`

| Disposition | Action |
|---|---|
| absent (not on live site) | `create` |
| `REFRESH`, `OPTIMISE`, `RETITLE` | `refresh` |
| `CONSOLIDATE`, `NOINDEX`, `DELETE_CANDIDATE` | `retire` |
| `KEEP` | `no_action` |

## Strategy pack (`content-strategy`)

```json
{
  "priority_queue": [
    {
      "url": "/blog/share-of-search",
      "title": "Share of search guide",
      "keyword": "share of search",
      "keywords": ["share of search", "share of search meaning"],
      "intent": "informational",
      "cluster": "measurement",
      "service": "Analytics",
      "subservice": null,
      "target_type": "service",
      "priority_tier": "quick_win",
      "priority": "Quick win",
      "opportunity_score": 78
    }
  ]
}
```

URL aliases: `url`, `suggested_url`, `path`.  
Keyword aliases: `keyword`, `primary_keyword`.  
Cluster aliases: `cluster`, `pillar`.  
Also accepted: `combined_priority_queue`, `priority_pages`.

## Architecture pack (`site-architecture`)

```json
{
  "target_url_tree": [
    {
      "url": "/blog/share-of-search",
      "parent": "/blog/",
      "depth": 2,
      "page_type": "article",
      "type": "article",
      "breadcrumb": ["Home", "Blog", "Share of search"],
      "indexable": true,
      "keyword": "share of search",
      "cluster": "measurement"
    }
  ],
  "cluster_owners": [
    {"cluster": "measurement", "owner_url": "/blog/share-of-search"}
  ]
}
```

URL aliases: `url`, `path`. Parent aliases: `parent`, `parent_url`.  
Service pages use `/{service}/`; sub-services use `/{service}/{subservice}/`.
Supporting blog rows may retain `/blog/{post}` while setting `parent` to the
relevant service/sub-service URL.
Cluster owners aliases: `cluster_owners`, `cluster_ownership` with `owner_url` / `canonical_owner_url` / `url`.

## Audit pack (`content-audit`)

```json
{
  "dispositions": [
    {
      "url": "/blog/share-of-search",
      "disposition": "REFRESH",
      "reason": "clicks down 78% YoY",
      "effort": "medium"
    }
  ]
}
```

Aliases: `inventory[]` with `path`/`url`, `disposition`, `reason`, `effort`.

## Output — `content_planning_report`

See SKILL.md. `pages[]` is the locked roadmap. `roadmap` is kept as an alias of `pages` for existing UI. `excluded[]` is never dropped.

Worked example: strategy URL `/seo` + architecture node `/seo` + no audit row → `action: create`, `disposition: null`.
