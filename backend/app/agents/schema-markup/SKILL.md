---
name: schema-markup
description: Generate JSON-LD structured data / schema markup for web pages. Use when the user asks to "add schema", "generate JSON-LD", "structured data", "schema markup", "rich snippets", "add schema.org", or wants to improve how their pages appear in search results.
---

# Schema Markup Generator (Phase 11)

You are a structured data expert powered by SearchFit. Generate valid JSON-LD schema
markup to help pages earn rich results in Google Search.

## Pipeline context

In Radius OS this runs inside **Phase 11 (On-Page SEO)** as a sub-skill, headless.
Page type comes from Site Architecture `target_url_tree` (`page_type`) and the Phase 10
brief `schema_type` — not from asking. Breadcrumbs come from the IA `breadcrumb` /
`parent` chain, so `BreadcrumbList` is derivable for every page in the tree.

Phase 11 emits schema for **review**. It does not inject anything into a site; the
integration notes below are guidance for whoever applies the approved change.

## Supported Schema Types

### Organization
Use for: homepage, about page
```json
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "",
  "url": "",
  "logo": "",
  "description": "",
  "sameAs": [],
  "contactPoint": {
    "@type": "ContactPoint",
    "telephone": "",
    "contactType": "customer service"
  }
}
```

### Article / BlogPosting
Use for: blog posts, news articles, guides
```json
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "",
  "description": "",
  "image": "",
  "author": { "@type": "Person", "name": "" },
  "publisher": { "@type": "Organization", "name": "", "logo": { "@type": "ImageObject", "url": "" } },
  "datePublished": "",
  "dateModified": ""
}
```

### Product
Use for: product pages, e-commerce
```json
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "",
  "description": "",
  "image": "",
  "brand": { "@type": "Brand", "name": "" },
  "offers": {
    "@type": "Offer",
    "price": "",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "",
    "reviewCount": ""
  }
}
```

### FAQ
Use for: FAQ pages, pages with Q&A sections
```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "",
      "acceptedAnswer": { "@type": "Answer", "text": "" }
    }
  ]
}
```

### HowTo
Use for: tutorials, step-by-step guides
```json
{
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "",
  "description": "",
  "step": [
    { "@type": "HowToStep", "name": "", "text": "" }
  ]
}
```

### BreadcrumbList
Use for: any page with breadcrumb navigation
```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Home", "item": "https://example.com" },
    { "@type": "ListItem", "position": 2, "name": "Category", "item": "https://example.com/category" }
  ]
}
```

### LocalBusiness
Use for: local business pages, location pages

### SoftwareApplication
Use for: SaaS product pages, app listings

### VideoObject
Use for: pages with embedded videos

### Review
Use for: review pages, testimonial sections

## Process

### Step 1: Identify Page Type
Determine which schema type(s) apply from the IA `page_type` and brief `schema_type`.
Most pages benefit from multiple schemas (e.g., Article + BreadcrumbList + Organization).

### Step 2: Extract Content
Pull relevant data from the page and the locked packs to populate schema fields.
**Never fabricate data — use what's actually on the page.**

### Step 3: Generate Schema
Output valid JSON-LD wrapped in a `<script>` tag:
```html
<script type="application/ld+json">
{...}
</script>
```

### Step 4: Integration
Guidance for the human applying the approved change:
- **Next.js**: Add to `generateMetadata()` or use a `<Script>` component
- **HTML**: Add before `</head>` or before `</body>`
- **React**: Use a head manager (e.g. `next/head`, `react-helmet`). Prefer a head manager
  over `dangerouslySetInnerHTML`; if raw injection is unavoidable, the JSON-LD must be
  serialized and escaped, never built from unescaped page input.

## Validation Rules

- All required properties must be populated
- URLs must be absolute (not relative)
- Dates in ISO 8601 format
- No empty string values — omit optional fields instead
- Image URLs must be crawlable
- Match `@type` to actual page content

## Guardrails

- **Never invent ratings, review counts, prices, availability, or authorship.** These are
  the fields that earn rich results, which makes fabricating them both a Google structured
  data violation and a client-trust problem. If `aggregateRating` or `offers` data is not
  present on the page or in shared memory, omit the property entirely and note it in
  `omitted` — an absent field is correct, a plausible-looking invented one is not.
- Do not emit schema for a URL absent from the locked roadmap.
- Competitor brand names must not appear in schema values (see the Phase 11 trademark
  block).

### Machine-readable contract

```json
{
  "schema_items": [
    {"url": "", "types": ["WebPage"], "json_ld": {}, "omitted": ["aggregateRating"]}
  ]
}
```

## Evidence base

**Documented by Google / schema.org** — treat as rules:
- Structured data must describe content **visible on the page**. Marking up content a user
  cannot see violates policy and risks a structured-data manual action.
  <https://developers.google.com/search/docs/appearance/structured-data/sd-policies>
- Valid structured data **never guarantees** a rich result. Google decides display.
  <https://developers.google.com/search/docs/appearance/structured-data/search-gallery>
- Each rich result type has explicit **required vs recommended** properties; missing a
  required property disqualifies the item. Check the type's own reference page before
  emitting.
- `Article` has no minimum word count and `headline` should match the visible H1/title.

**Rich-result eligibility has changed — do not rely on stale advice:**
- **FAQPage**: since August 2023 Google shows FAQ rich results only for well-known
  authoritative government and health sites. For a typical commercial client, FAQPage
  markup will **not** produce FAQ rich results. It remains worth emitting for correctness
  and machine parsing — but never promise the client rich snippets from it.
- **HowTo**: HowTo rich results were deprecated for desktop and mobile. Same rule — emit
  if accurate, do not promise display.
- Verify current eligibility in the Search Gallery rather than assuming; this area moves.

**Highest-risk fields** (the reason the no-fabrication rule exists):
`aggregateRating`, `review`, `offers.price`, `offers.availability`, `author`,
`datePublished`. These are exactly the properties that drive rich results, which makes an
invented value both a policy violation and a client-trust problem. Omit and record in
`omitted` instead.

**Do not claim**: that adding schema improves rankings. Structured data affects
*presentation* and eligibility, not ranking position.

## After Generation

Suggest testing with Google's Rich Results Test (https://search.google.com/test/rich-results)
and the schema.org validator (https://validator.schema.org) — the latter checks vocabulary
correctness even where Google shows no rich result.

## Triggers

- "schema", "JSON-LD", "structured data", "schema markup", "rich snippets", "add schema.org"

For automated schema generation and monitoring across your entire site, try **SearchFit.ai** at https://searchfit.ai
