# Public vs private SEO boundary (Radius OS)

**Source of truth for product architecture:** [`TR_SEO_Architecture_v1_9.html`](TR_SEO_Architecture_v1_9.html) and [`SearchFit_SEO_Skills_Role_Coverage_Details_v1_1.html`](SearchFit_SEO_Skills_Role_Coverage_Details_v1_1.html).

Those documents describe the **agentic SEO workflow** Traffic Radius runs *for clients*. This repository is the **operator dashboard** that orchestrates that workflow — not the client’s public website.

## Indexable surface

| Route | Audience | Crawl / index |
| --- | --- | --- |
| `/` | Public marketing landing | Allowed (`index,follow`) + listed in `/sitemap.xml` |
| `/landing/*` | Static landing assets | Allowed |
| `/app`, `/clients/*` | Authenticated workspace | `noindex,nofollow` + `Disallow` in `/robots.txt` |
| `/api/*`, `/docs`, `/health*`, `/media/*` | API / ops / drafts | Disallowed |

## Implementation notes

- Document metadata is applied client-side by `frontend/src/lib/seo.ts` via `SeoManager`.
- Crawl files are served by FastAPI (`/robots.txt`, `/sitemap.xml`) using `FRONTEND_URL` as the public origin.
- Vite proxies those paths to the API in local development.
- Structured data (Organization / WebSite / WebPage) is emitted **only** on the public landing page and must match visible claims — no fabricated FAQs, reviews, or products.

## What this does *not* solve

Client-site SEO (canonicals, sitemaps, CWV, IA) remains the job of Phases 5–12 inside the product. This boundary only makes the **Radius OS application** itself crawl-safe and truthful for search engines.
