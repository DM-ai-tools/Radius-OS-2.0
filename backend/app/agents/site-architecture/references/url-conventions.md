# URL conventions

## Structure
- Prefer **subfolders** over subdomains unless content is a separate property (ops call, not ranking)
- Directory path should match real hierarchy so breadcrumbs and URLs agree
- Every intermediate directory needs a real useful page — no phantom folders
- One page = one intent = one URL
- Depth follows taxonomy; never add/remove folders purely for SEO / slash count

## Slugs
- Lowercase, hyphens not underscores, audience language
- 3–5 words, primary keyword, drop stop words unless meaning changes
- No dates, no CMS artefacts (`?p=123`, `/index.php`), no file extensions
- Stable — describe the page, not the campaign year

## Policy (pick explicitly)
- Trailing slash: one convention, redirect the other
- `www` vs apex: one host, redirect the other
- Case: lowercase + redirect
- Pagination: `/page/2/` self-canonical; never to page 1
- Locale: consistent `/en-au/` style subfolders
- Legacy paths: keep and redirect; never reuse for new content

## Faceted navigation
- Not indexable → robots disallow parameter patterns or use URL fragments
- Indexable → `&` separators, fixed filter order, real 404 for empty combos
- Allow at most one indexable facet by default
