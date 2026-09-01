# Routing: content-strategy vs. content-audit

These two skills cover one workflow from opposite ends. Never treat them as
alternatives — decide which end the request is at, and expect to use both on
any site that already has content.

**content-audit** — the site's EXISTING pages. Performance, decay,
cannibalisation, and a keep/refresh/optimise/retitle/consolidate/noindex/
delete verdict per URL. Requires Search Console data for two comparable
periods. Signals: "audit", "prune", "content decay", "traffic dropped", "old
posts", "which pages should I delete", "cannibalisation", "thin content",
"should I update or rewrite".

**content-strategy** — content that DOESN'T EXIST YET. Topical authority map,
missing topics, intent mapping, prioritisation, editorial calendar. Signals:
"plan content", "what should I write about", "content calendar", "roadmap",
"editorial plan".

## Order of operations

**Radius OS runtime chain:** Content Strategy → Site Architecture → Technical SEO →
**Content Audit** → Content Planning (join). Strategy is forward-looking; this audit
dispositions what already exists so Planning can merge both.

When audit and strategy compete for the same hours on an established site, prefer
audit REFRESH / CONSOLIDATE over net-new pages on the same intent — recovering
proven demand is cheaper than a cold keyword. Where the audit returns REFRESH or
CONSOLIDATE on a topic the strategy was about to plan new content for, plan the
refresh and do not create a new URL.

Only skip the audit when the site is genuinely new (greenfield Planning may omit
it), or when Search Console access is unavailable. In the second case say so
explicitly: without performance data, decay and cannibalisation cannot be assessed.

## Shared vocabulary — do not restate or contradict

- The **intent classification table** lives in content-strategy Step 4.
  content-audit uses it to judge intent mismatch; it does not define its own.
- The **prioritisation matrix** (quick wins / big bets / fill-ins / avoid)
  lives in content-strategy Step 5. content-audit maps its effort tags onto
  that matrix rather than inventing a second scoring model.
- **Cannibalisation** has two distinct causes with different owners.
  Structural (two URLs built for one intent, a taxonomy problem) belongs to
  site-architecture. Performance-based (two URLs both drawing impressions on
  a shared query) belongs to content-audit. If both skills produce a verdict
  on the same URL, site-architecture's decision wins, because changing the
  URL invalidates the performance history the audit reasoned from.

## Combined output

When both run in one engagement, deliver ONE prioritised queue, not two
documents. Interleave by expected return and effort:

1. Cannibalisation merges (audit) — changes which URLs exist
2. Refresh queue (audit) — proven demand, decaying
3. Quick wins: retitle and striking-distance pages (audit)
4. Genuine content gaps (strategy) — topics with no page at all
5. Consolidations and index cleanup (audit)

Never let a new-content recommendation appear above a refresh for the same
topic. If the queue contains both, they are the same item and the refresh
is the answer.
