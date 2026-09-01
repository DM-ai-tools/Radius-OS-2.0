# Content Audit — Evidence Base

Read this before recommending removals. Pruning advice from SEO blogs and Google's public position often diverge; Radius defaults to Google's quality-over-age framing.

## Quality, not age

- **Do not delete because a page is old.** Google's Danny Sullivan addressed CNET-style archive culls: deleting content because you believe Google dislikes old content is not supported by Google's guidance. Older pages can still be helpful and rank.
- **Do remove or improve unhelpful content.** Helpful-content guidance: if traffic dropped, self-assess as a visitor would — keep what helps, improve or remove what doesn't.
- **Never prune by publication date alone.** Thin, unhelpful, non-ranking pages are candidates; dated but useful pages are not.

## Sitewide effects are easy to oversell

- Removing one outdated URL on a huge site *may* free crawl attention for other URLs.
- It does **not** mean Google suddenly views the whole site as better.
- On a ~500-page site, deletion is rarely the primary lever — improve and consolidate first.

## Prefer reversible actions

| Action | When | Risk |
|--------|------|------|
| Refresh / rewrite | Proven demand, decaying clicks | Low — keeps URL equity |
| Consolidate + 301 | Competing URLs, shared queries | Medium — needs correct redirect map |
| `noindex` | Zero search value but substantial / uncertain | Low — reversible |
| Delete | Confirmed: no links, no conversions, no business/legal purpose | High — irreversible |

**DELETE_CANDIDATE is a review queue, never an auto-delete.** Check backlinks, conversions, support/legal use, and seasonality before removal.

## Decay diagnosis traps

1. **Seasonality** — YoY same window beats prior-90 vs current-90 when niches are seasonal.
2. **Rendering / technical** — a deploy that broke CSR or indexing can look like "content decay." Check `rendering-audit` / Technical SEO before a sitewide editorial cull.
3. **Cannibalisation** — two pages splitting one query both look weak; fix ownership first or refresh verdicts are wrong.
4. **Missing GSC prior period** — without two comparable periods you cannot claim decay; call the audit qualitative.

## Primary sources to cite in client write-ups

- Google Search Central — helpful content / self-assessment guidance
- Danny Sullivan clarifications on archive pruning and outdated pages (public Search Liaison commentary)
- GSC API limits: 25k rows/request; ~50k rows/day/property/search type; privacy-filtered rare queries

Update this file when SearchFit / Google guidance changes; keep thresholds in the skill and script, not here.
