# batch_llm_client

Token-aware LLM batching for Radius OS. Packs items so each request is as full as safely possible, maps every output back to its input id, retries transient failures, and resumes from a SQLite checkpoint after a crash.

## Quick start

```python
from app.integrations.batch_llm import process_items

result = await process_items(
    items=[
        {"id": "kw-1", "text": "mortgage broker sydney"},
        {"id": "kw-2", "text": "home loan comparison"},
    ],
    model_key="keyword_cluster",  # see backend/config/llm_limits.yaml
    prompt_template="Keep relevant business keywords. Drop junk.",
    options={
        "mode": "realtime",  # or async_batch | auto
        "expected_output_tokens_per_item": 40,
        "checkpoint_key": "phase5-relevance-client-123",
        "budget_usd": None,  # or a float ceiling
        "use_prompt_cache": True,
    },
)

print(result.results)   # {"kw-1": ..., "kw-2": ...}
print(result.failed)    # dead-letter entries
print(result.summary)   # tokens, cost, retries
```

`prompt_template` may also be a callable:

```python
def build(items):
    system = "Return JSON only."
    user = "\n".join(f"{it.id}: {it.text}" for it in items)
    return system, user
```

## Model keys

Defined in `backend/config/llm_limits.yaml`:

| key | Typical use |
|---|---|
| `router` | Chat agent routing |
| `skill` | Default Claude synthesis |
| `competitor` | Gemini competitor scoring |
| `research` | Perplexity / Sonar |
| `write` | Long-form drafts |
| `keyword_cluster` | Keyword filter / cluster LLM |
| `image` | Image model (no chat batch) |

Aliases map bare env ids (`claude-haiku-4-5`) to OpenRouter-canonical ids.

## Adding a model

1. Add a block under `models:` in `llm_limits.yaml` (context, max output, prices, tokenizer, `supports_batch`).
2. Optionally add an `aliases:` entry for the env string you already use.
3. Point a Settings field at it, or pass `options={"model_id": "..."}`.
4. No Python change required unless you need a new adapter (see `adapters/`).

## Tuning

| Setting | Where | Meaning |
|---|---|---|
| `safety_margin` | yaml `defaults` | Extra headroom on reserved output (default 0.12) |
| `max_retries` | yaml / options | Transient retries before dead-letter |
| `overlap_tokens` | yaml | Overlap when splitting oversized items |
| `max_concurrency` | yaml / options | Parallel realtime batches |
| `mode` | options | `realtime` / `async_batch` / `auto` |
| `auto_batch_min_items` | yaml | When `auto` switches to Batch API |
| `budget_usd` | options | Abort with `BudgetExceeded` if estimate is higher |
| `rpm` / `tpm_*` | per model in yaml | Fill from your account; leave null if UNKNOWN |

## Checkpoint / dead-letter

Progress lives under `data/batch_llm_checkpoints/` (configurable). Re-running the same `checkpoint_key` skips completed ids and never silently drops failures — they go to the dead-letter table in the same SQLite file.

## Migration list (existing call sites)

Do not migrate blindly; keep one-shot chat turns on `synthesize_json`. Prefer `process_items` where **many homogeneous items** are scored or filtered.

| Location | Current behavior | Proposed change |
|---|---|---|
| `ml/tier_competitors.py` `score_entity_live` | 1 Gemini call per competitor | Batch competitors into `process_items(..., model_key="competitor")` with JSON keyed by competitor id |
| `services/keyword_llm_relevance.py` | Custom per-seed packing via `_openrouter_chat` | Replace packing with `process_items` / shared `pack_batches`; keep seed grouping |
| `services/keyword_llm_clustering.py` | One large OpenRouter call | Use packing + truncation split; `model_key="keyword_cluster"` |
| `services/content_brief.py` `_enrich_brief_llm` | 1 call per brief | Batch briefs for a roadmap pass |
| `integrations/providers.py` `discover_competitors` | Single JSON | Optional batch of research facets; usually leave as one-shot |
| `services/create_topic.py` | One JSON | Leave one-shot unless multi-cluster topic fan-out |
| `services/create_content.py` | One write + images | Leave write one-shot; images stay on image adapter |
| `services/phase_validation/semantic.py` | One JSON | Leave one-shot |
| `services/discovery_gap_fill.py` | One JSON | Leave one-shot |
| `services/content_strategy.py` / `site_architecture.py` | One JSON | Leave one-shot |
| `integrations/llm.py` `route_agent` | 40-token router | Do **not** batch |
| `services/intake_ai.py` / `site_research.py` | Research | Leave one-shot (`research` has no batch variant) |

## Tests

```bash
cd backend
pytest tests/test_batch_llm.py -q
```
