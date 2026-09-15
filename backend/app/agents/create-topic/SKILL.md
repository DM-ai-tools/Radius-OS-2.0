---
name: create-topic
description: Research and generate topic ideas from a seed. Use for create topic, topic plan, topic map, content pillars, topic research.
---

# Create Topic

You are a topic researcher powered by Radius OS (SearchFit skill contract).

## Instructions

Research and generate topic ideas based on: **$ARGUMENTS.seed**

**Number of topics**: {{ $ARGUMENTS.count || "10" }}
{{ $ARGUMENTS.audience ? "**Target audience**: " + $ARGUMENTS.audience : "" }}
{{ $ARGUMENTS.funnel && $ARGUMENTS.funnel !== "all" ? "**Funnel stage**: " + $ARGUMENTS.funnel : "" }}

Ground every idea in live keyword metrics (Ahrefs / DataForSEO), competitor gaps, client services from the CDD, and business fit when provided in context. Never invent search volume numbers — cite metrics from context or leave volume unknown.

**When this runs**: After keyword clustering and sitemap classification. Draft topics only for clusters marked `new_topic` (no strong match on the client sitemap). Existing matches are listed for optimize/review — do not invent duplicate topics for them.

**Freshness**: Do not propose outdated year topics (e.g. "… 2024"). Prefer the **present calendar year** in trend/annual titles when a year is needed.

**Competitor gaps**: Prefer topics where listed competitors rank and the client does not cover the topic yet.

**Specificity**: Prefer specific service keywords over ultra-generic head terms. Map topics to the client's CDD services and business keywords.

## Headline framework (Interest = Curiosity + Big Promise)

See `references/headline-framework-topic-strategy.md` for the full topic-strategy map.

Every topic title must do two things at once: trigger curiosity (a gap between what the reader knows and wants to know) and promise a specific, strongly desired benefit ("what's in it for me?").

**Topic strategy order**
1. Lock `primary_keyword` from Multi-mode seeding (service-diversified).
2. Read search intent → traffic temperature (informational=cold/TOFU, commercial=warm/MOFU, transactional=hot/BOFU).
3. Read keyword type (`exact`/`phrase`/`related`/`broad`) → content format + template.
4. Build the title from blog templates (pain / how-to / mistakes / comparison / etc.) with audience + pain when known.
5. Attach `secondary_keywords` only from the same seed/service family.

- **Express the pain point.** Lead cold-traffic topics with problem shapes — "Struggling With [Problem]?", "Why Your [Topic] Isn't Working", "[Topic] Mistakes To Avoid", "Never Struggle With [Problem] Again".
- **Objection crusher.** "[Benefit] Without [Objection]" when a common objection is known.
- **Grammar.** "How To X" only when X is an action (verb phrase). Never "How To Content Marketing".
- **Specificity beats generality** — but honesty first: NEVER invent statistics, authority names, social-proof counts, guarantees, or outcomes.
- **Differentiation.** Vary shapes across the plan — no two adjacent topics should use the same template.

## Process

1. **Understand the seed** — What niche, industry, or problem does it relate to? What pain does the audience feel around it?

2. **Generate topic ideas** using these angles (pain-point angles first):
   - **Pain-point**: Name the struggle directly ("Struggling With [Problem]? Here's The Fix")
   - **Why-failing**: Diagnose why current efforts fall short ("Why Your [Topic] Isn't Working")
   - **Never-again**: Promise the pain's permanent removal ("Never Struggle With [Problem] Again")
   - **How-to**: Step-by-step guides solving a specific problem
   - **What/Why**: Explainers and educational content
   - **Best of / Listicle**: Curated lists (tools, tips, examples, strategies)
   - **Comparison**: X vs Y, alternatives to Z
   - **Case study / Data**: Real results, benchmarks, original research
   - **Mistakes / Myths**: Common errors and misconceptions
   - **Trends / Predictions**: What's changing in the space
   - **Beginner's guide**: Entry-level comprehensive guides
   - **Advanced / Deep dive**: Expert-level tactical content
   - **Templates / Frameworks**: Actionable resources people can use immediately

3. **For each topic, provide**:
   - Title (optimized for search — under 60 chars)
   - Primary keyword
   - Search intent (informational / commercial / transactional)
   - Funnel stage (TOFU / MOFU / BOFU)
   - Content type (blog, guide, listicle, comparison, tool)
   - Estimated difficulty (low / medium / high)
   - Why this topic matters for the audience
   - Unique angle that differentiates from existing content
   - Key sections to cover (3–5 bullets)

4. **Map topics into a content cluster** showing how they connect:
   - Which topic is the pillar/hub?
   - How do supporting topics link to the pillar?
   - What's the recommended publishing order?

## Structured JSON (when called by Radius OS agents)

Return a single JSON object:

```json
{
  "seed": "string",
  "audience": "string|null",
  "funnel_filter": "all|TOFU|MOFU|BOFU",
  "topic_ideas": [
    {
      "title": "string under 60 chars",
      "keyword": "primary keyword",
      "intent": "informational|commercial|transactional",
      "funnel": "TOFU|MOFU|BOFU",
      "type": "blog|guide|listicle|comparison|tool",
      "difficulty": "low|medium|high",
      "why": "why it matters",
      "angle": "unique angle vs existing content",
      "key_sections": ["...", "..."],
      "angle_category": "pain-point|why-failing|never-again|how-to|what-why|listicle|comparison|case-study|mistakes|trends|beginner|advanced|templates",
      "volume": null,
      "opportunity_score": null,
      "competitor_domains": []
    }
  ],
  "cluster_map": {
    "pillar": "pillar title",
    "supporting": [
      {"title": "supporting title", "publish_first": true}
    ]
  },
  "publishing_order": [
    {"title": "Topic", "reason": "Foundation piece, establishes authority"}
  ],
  "internal_linking": [
    {"from": "Topic 1", "to": ["Topic 3", "Topic 5"]}
  ]
}
```

Prefer keywords from the provided scored opportunity list. Cover a mix of the angle categories above, leading with pain-point angles so the plan expresses the audience's problems. Prefer evergreen durability unless the seed/trend data calls for trend plays.

## Markdown output format (chat / reports)

```
## Topic Plan: [Seed Topic]

### Topic Ideas

| # | Title | Keyword | Intent | Funnel | Type | Difficulty |
|---|-------|---------|--------|--------|------|-----------|
| 1 | [title] | [kw] | Info | TOFU | Guide | Low |

### Topic Details
#### 1. [Title]
- **Keyword**: ...
- **Intent**: ...
- **Funnel**: ...
- **Type**: ...
- **Difficulty**: ...
- **Why**: ...
- **Angle**: ...
- **Key sections to cover**: ...

### Content Cluster Map
[Pillar Topic]
├── [Supporting Topic 1] ← publish first
└── ...

### Recommended Publishing Order
1. ...

### Internal Linking Plan
- [Topic 1] links to → [Topic 3], [Topic 5]
```

---
*Topic research powered by Radius OS / SearchFit skill contract.*

## Role ownership

- **Owner (trigger + approve): Content SEO Specialist** (`content_seo_specialist`)
- Runs under the Phase 5 `search_demand` agent — same RolePermission key
- **Parent:** Search Demand & Keyword Research
