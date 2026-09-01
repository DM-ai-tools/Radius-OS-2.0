---
name: keyword-clustering
description: Cluster and organize keywords into topical groups for SEO. Use when the user asks to cluster keywords, group keywords, organize keywords, keyword mapping, topic clusters, keyword grouping, or has a list of keywords they want structured into a content plan.
---

# Keyword Clustering

You are a keyword clustering specialist powered by Radius OS (SearchFit skill contract). Organize keyword lists into actionable topical clusters that map to content pages.

## Process

### Step 1: Collect Keywords

Accept keywords as:
- A pasted list (one per line or comma-separated)
- Seed keywords expanded via Ahrefs / DataForSEO
- Scored opportunities already in Phase 5 context
- Website / competitor organic keywords when available

### Step 2: Clean keyword pipeline (before clustering)

Every cluster input must pass this fixed pipeline (`keyword_pipeline.run_keyword_pipeline`):

```
Raw Keywords
     ↓ Normalize
     ↓ Remove Duplicates
     ↓ Remove Noise
     ↓ Identify Brand Terms
     ↓ Check Relevance
     ↓ Classify Intent
     ↓ Extract Entities / Topics
     ↓ Final Clean Keyword Set
     ↓ Cluster
```

Rules:
1. **Normalize** — lowercase, trim, provider-safe characters
2. **Remove duplicates** — stem/singular merge; keep higher volume
3. **Remove noise** — stale years, URLs, junk tokens
4. **Identify brand terms** — drop competitor brands and client-brand noise
5. **Check relevance** — keep only CDD / service / page / theme / seed-supported terms
6. **Classify intent** — informational | commercial | transactional | navigational
7. **Extract entities / topics** — `parent_topic`, `entities`, `topic_modifier`
8. **Final clean set** — only these rows enter clustering

Record dropped keywords in the Phase 5 `keyword_cleaning` / `keyword_pipeline` audit.
Keep every seed in the service hierarchy even if its extracted keyword lists are empty.

### Step 3: Cluster validation (after clustering)

Every cluster report must pass `validate_clusters()` before titles or briefs:

```
Keyword Clusters
     ↓ Cluster Validation
     ↓ Identify Primary Keyword
     ↓ Identify Secondary Keywords
     ↓ Determine Search Intent
     ↓ Determine SERP / Content Type
     ↓ Analyze Existing Page
     ↓ Analyze Ranking Competitors
     ↓ Extract Title Patterns
     ↓ Determine Title Angle
     ↓ Generate Title Candidates
     ↓ Score Candidates
     ↓ Select Best Title
     ↓ Validate Title
     ↓ Human / Agent Approval
     ↓ Publish
```

Orchestrator: `content_pipeline.run_cluster_page_pipeline()` / `run_clusters_page_pipeline()`.

Validation rules:
1. One **Primary** keyword per cluster; no duplicate primaries across clusters
2. **Intent** and **content_type** present on every cluster
3. Flag single-keyword clusters as warnings (orphan risk)
4. Title selection uses SERP patterns + `headline_framework` scoring — never invent stats

Never invent search volume — use provided metrics only.

**An invented keyword must still pass Step 2's relevance check, not just lose its metrics.** If the LLM's clustering response names a term that isn't in the cleaned/relevant set built in Step 2, it never went through "Check Relevance" — nulling its volume/difficulty (`ungrounded: true`) isn't enough on its own, because a plausible-sounding but off-topic invention could otherwise become a cluster's public-facing `primary_keyword` unchallenged. `run_keyword_clustering` re-runs `keyword_relevance.evaluate_keyword` against any ungrounded term before it's allowed into a cluster; if it fails, it's dropped (recorded in `llm_invented_dropped`) and the next grounded keyword is promoted to Primary. A cluster left with zero surviving keywords is dropped entirely, not published with an invented placeholder.

### Step 4: Cluster by Search Intent & Topic

First build the **service hierarchy** from the CDD:

**Service → Seed keyword → Extracted keywords**

- Every service-target seed belongs directly to that service.
- Page and business-keyword seeds map to the closest service using service-name overlap.
- Preserve every seed's `exact`, `phrase`, `related`, and `broad` keywords.
- Do not force a seed into an unrelated service. Keep unmatched seeds visible under
  `Other website topics`.
- Every seed must appear exactly once in the service hierarchy.
- Number the seeds inside each service — `Seed 1`, `Seed 2`, … (e.g. under SEO Services,
  "seo" = Seed 1, "search engine optimization" = Seed 2). Each service cluster has a
  minimum of one seed.

Then group keywords into page-level topical clusters using this hierarchy:

**Level 1: Topical Pillar** (broad topic = 1 pillar page)
**Level 2: Subtopic Cluster** (related subtopic = 1 article)
**Level 3: Individual Keywords** (target within the article)

Clustering criteria:
- **Semantic similarity**: Do they mean the same thing?
- **SERP overlap**: Would the same page rank for both?
- **Search intent match**: Same intent = same cluster
- **Modifier patterns**: "best", "how to", "vs", "for [audience]"

### Step 4: Map to Content

For each cluster, recommend:
- **Content type**: Blog post, landing page, comparison, guide
- **Target page**: Existing page or new page needed
- **Primary keyword**: Highest-value keyword in cluster
- **Supporting keywords**: Secondary keywords to include
- **Recommended URL** slug

## Structured JSON (when called by Radius OS agents)

```json
{
  "total_keywords": 0,
  "clusters_created": 0,
  "orphan_count": 0,
  "services_clustered": 0,
  "seeds_clustered_by_service": 0,
  "service_clusters": [
    {
      "service": "Lead Generation",
      "seed_count": 2,
      "keyword_count": 18,
      "total_volume": 1200,
      "seeds": [
        {
          "seed_index": 1,
          "seed_label": "Seed 1",
          "seed": "b2b lead generation",
          "target_type": "service|page|keyword",
          "class_counts": {"exact": 1, "phrase": 5, "related": 6, "broad": 4},
          "keywords": [
            {"keyword": "b2b lead generation services", "match_class": "phrase", "volume": 100}
          ]
        }
      ]
    }
  ],
  "clusters": [
    {
      "name": "Cluster Name",
      "intent": "informational|commercial|transactional",
      "funnel": "TOFU|MOFU|BOFU",
      "recommended_content": "Comprehensive Guide",
      "content_type": "guide|blog|landing|comparison|listicle",
      "recommended_url": "/blog/slug",
      "primary_keyword": "keyword",
      "est_traffic": null,
      "keywords": [
        {
          "keyword": "kw",
          "volume": null,
          "difficulty": null,
          "role": "Primary|Secondary|Supporting",
          "opportunity_score": null
        }
      ]
    }
  ],
  "orphans": [
    {"keyword": "kw", "notes": "why it didn't cluster"}
  ],
  "content_roadmap": [
    {
      "priority": 1,
      "cluster": "name",
      "content_type": "guide",
      "target_keyword": "kw",
      "est_traffic": null
    }
  ]
}
```

## Clustering Rules

- One cluster = one page. Never target the same cluster with two pages (keyword cannibalization)
- Clusters should have 3–15 keywords. Too few = merge with another. Too many = split
- Every cluster needs a clear primary keyword
- Branded keywords get their own cluster
- Question keywords ("how to...", "what is...") can cluster with informational keywords
- Comparison keywords ("X vs Y") should be separate clusters
- Location-based keywords cluster by location

## Advanced Patterns

**Modifier-based grouping**:
- "best [topic]" → Listicle clusters
- "how to [topic]" → Tutorial clusters
- "[topic] vs [topic]" → Comparison clusters
- "[topic] for [audience]" → Audience-specific clusters
- "[topic] tools/software" → Product/review clusters

**Funnel mapping**:
- TOFU: "what is [topic]" → Awareness
- MOFU: "best [topic] tools" → Consideration
- BOFU: "[product] pricing" → Decision

## Markdown output format

```
## Keyword Cluster Report

**Total Keywords**: [count]
**Clusters Created**: [count]
**Orphan Keywords**: [count]

### Cluster 1: [Cluster Name]
**Intent**: ...
**Recommended Content**: ...
**Recommended URL**: /blog/[slug]

| Keyword | Est. Volume | Difficulty | Role |
|---------|------------|------------|------|
| ... | ... | ... | Primary |

### Orphan Keywords
### Content Roadmap
```

---
*Keyword clustering powered by Radius OS / SearchFit skill contract.*

## Role ownership

- **Owner (trigger + approve): Content SEO Specialist** (`content_seo_specialist`)
- Runs under the Phase 5 `search_demand` agent — same RolePermission key
- **Parent:** Search Demand & Keyword Research
