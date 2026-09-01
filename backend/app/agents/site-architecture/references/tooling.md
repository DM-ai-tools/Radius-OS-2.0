# Tooling — running the audit and reading the output

All open-source. Nothing here needs a paid licence or an API key.

## Stack

| Tool | Role | Licence |
|---|---|---|
| [advertools](https://github.com/eliasdabbas/advertools) | Scrapy-based SEO crawler | MIT |
| [networkx](https://networkx.org/) | Internal link graph, PageRank | BSD |
| pandas | Tabular analysis | BSD |

Radius OS also runs an **in-app lightweight BFS audit** via `app.services.site_architecture` when advertools isn't installed.

```bash
pip install advertools networkx pandas
```

## Running it

From repo `backend/`:

```bash
# Discovery crawl
python scripts/architecture_audit.py crawl https://example.com crawl.jl --page-cap 5000 --delay 0.25

# Analysis
python scripts/architecture_audit.py audit crawl.jl --domain example.com --out ./ia
```

Start with `--page-cap 500` on an unfamiliar site. Raise `--delay` on fragile hosting.

### Two gotchas

1. **`crawlytics.links()` needs `links_nofollow`** — the script backfills when missing.
2. **`depth` is click depth from the seed URL**, not folder count. Folder depth is `dir_depth` (readability only).

## Artifacts

| File | Act when |
|---|---|
| `summary.csv` | Always read first |
| `click_depth_distribution.csv` | Long tail at 4+ |
| `pages_depth_4_plus.csv` | Any money page appears |
| `directory_inventory.csv` | Two directories serve one intent |
| `phantom_directories.csv` | Any taxonomy phantom |
| `orphan_pages.csv` | Any indexable orphan |
| `redirect_chains.csv` | Any multi-hop |
| `canonical_conflicts.csv` | Feeds cluster ownership |

## Crawl etiquette

Respect robots.txt. Keep concurrency low. Identify the crawler honestly. Prefer off-peak crawls on production.
