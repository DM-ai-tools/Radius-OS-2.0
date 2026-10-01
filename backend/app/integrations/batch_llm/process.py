"""process_items — token-aware, checkpointed LLM batching."""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.config import get_settings
from app.integrations.batch_llm.adapters.anthropic import AnthropicAdapter
from app.integrations.batch_llm.adapters.base import LLMAdapter
from app.integrations.batch_llm.adapters.openrouter import OpenRouterAdapter
from app.integrations.batch_llm.checkpoint import CheckpointStore, DeadLetterEntry
from app.integrations.batch_llm.limits import (
    defaults,
    effective_model_id,
    model_spec,
)
from app.integrations.batch_llm.packing import (
    Item,
    PackedBatch,
    build_batch_user_prompt,
    estimate_tokens,
    pack_batches,
    split_oversized,
)
from app.integrations.batch_llm.rate_limit import RateLimitState
from app.logging_config import get_logger

log = get_logger("batch_llm")


class BudgetExceeded(RuntimeError):
    def __init__(self, estimate_usd: float, ceiling: float):
        super().__init__(
            f"Estimated cost ${estimate_usd:.4f} exceeds budget ceiling ${ceiling:.4f}"
        )
        self.estimate_usd = estimate_usd
        self.ceiling = ceiling


@dataclass
class BatchResult:
    results: dict[str, Any]
    failed: list[DeadLetterEntry]
    summary: dict[str, Any]


def _parse_results_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    if not raw.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("response_not_object")
    if "results" in data and isinstance(data["results"], dict):
        return data["results"]
    # Allow flat id→value map
    return data


def _estimate_cost_usd(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    model_key: str,
    batch_discount: bool,
) -> float:
    spec = model_spec(model_key)
    inp = float(spec.get("price_input_per_m") or 0)
    out = float(spec.get("price_output_per_m") or 0)
    mult = float(spec.get("price_batch_multiplier") or 1.0) if batch_discount else 1.0
    return ((prompt_tokens / 1_000_000) * inp + (completion_tokens / 1_000_000) * out) * mult


def _pick_adapter(model_key: str) -> LLMAdapter:
    settings = get_settings()
    spec = model_spec(model_key)
    # Skills / router → Anthropic. Research/images keep OpenRouter elsewhere.
    if model_key in ("router", "skill") and settings.anthropic_api_key:
        return AnthropicAdapter()
    if settings.openrouter_api_key:
        return OpenRouterAdapter()
    if settings.anthropic_api_key:
        return AnthropicAdapter()
    return OpenRouterAdapter() if "openrouter" in (spec.get("env_keys") or []) else AnthropicAdapter()


def _normalize_items(items: list[dict[str, Any]]) -> list[Item]:
    out: list[Item] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each item must be a dict with id and payload/text")
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            raise ValueError("item missing id")
        if item_id in seen:
            raise ValueError(f"duplicate item id: {item_id}")
        seen.add(item_id)
        text = raw.get("text")
        if text is None:
            payload = raw.get("payload")
            text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        out.append(Item(id=item_id, text=str(text), payload=raw.get("payload")))
    return out


async def _sleep_backoff(attempt: int, retry_after: float | None = None) -> None:
    if retry_after is not None:
        await asyncio.sleep(max(0.5, retry_after) + random.uniform(0, 0.25))
        return
    base = min(60.0, (2**attempt) + random.uniform(0, 0.5))
    await asyncio.sleep(base)


async def process_items(
    items: list[dict[str, Any]],
    model_key: str,
    prompt_template: str | Callable[[list[Item]], tuple[str, str]],
    options: dict[str, Any] | None = None,
    *,
    adapter: LLMAdapter | None = None,
) -> BatchResult:
    """Batch LLM processing with packing, retries, and checkpoint resume.

    ``prompt_template``:
      - str: used as the system prompt; user message is auto-built with item ids
      - callable(items) -> (system, user)
    """
    opts = dict(options or {})
    cfg = defaults()
    mode = str(opts.get("mode") or cfg.get("mode") or "realtime")
    expected_out = int(opts.get("expected_output_tokens_per_item") or 256)
    budget = opts.get("budget_usd")
    if budget is None:
        budget = cfg.get("budget_usd_ceiling")
    checkpoint_key = str(opts.get("checkpoint_key") or f"{model_key}-default")
    max_retries = int(opts.get("max_retries") or cfg.get("max_retries") or 5)
    concurrency_base = int(
        opts.get("concurrency") or cfg.get("max_concurrency") or 4
    )
    use_cache = bool(opts.get("use_prompt_cache", True))
    validate = opts.get("validate")  # optional callable(id, value) -> bool

    spec = model_spec(model_key)
    model_id = effective_model_id(model_key, opts.get("model_id"))
    # Prefer live settings model when present
    settings = get_settings()
    field = spec.get("settings_field")
    if field and not opts.get("model_id"):
        live = getattr(settings, str(field), None)
        if live:
            from app.integrations.batch_llm.limits import resolve_model_id

            model_id = resolve_model_id(str(live))

    tokenizer = str(spec.get("tokenizer") or "gemini_approx")
    store = CheckpointStore(checkpoint_key)
    limiter = RateLimitState(
        rpm=spec.get("rpm"),
        tpm_in=spec.get("tpm_in"),
        tpm_out=spec.get("tpm_out"),
    )
    client = adapter or _pick_adapter(model_key)

    try:
        normalized = _normalize_items(items)
        done = store.completed_ids()
        pending = [it for it in normalized if it.id not in done]

        # Split oversized before packing
        context = int(spec.get("context_window") or 128000)
        max_out = int(spec.get("max_output_tokens") or 4096)
        margin = float(cfg.get("safety_margin") or 0.12)
        # Leave room for system + one item output
        max_item = max(
            256,
            int(context * 0.6) - int(expected_out * (1 + margin)),
        )
        expanded: list[Item] = []
        chunk_parents: dict[str, str] = {}
        for it in pending:
            chunks = split_oversized(
                it, max_item_tokens=max_item, tokenizer=tokenizer
            )
            if len(chunks) == 1 and chunks[0].chunk_count == 1:
                expanded.append(it)
            else:
                for ch in chunks:
                    expanded.append(Item(id=ch.id, text=ch.text, payload=it.payload))
                    chunk_parents[ch.id] = it.id

        # Cost estimate (realtime prices; batch discount applied only if mode forces batch)
        if isinstance(prompt_template, str):
            system_est = prompt_template
        else:
            system_est, _ = prompt_template(expanded[:1] or [Item(id="_", text="")])
        est_in = estimate_tokens(system_est, tokenizer) * max(1, len(expanded) // 4 + 1)
        est_in += sum(estimate_tokens(it.text, tokenizer) for it in expanded)
        est_out = expected_out * max(1, len(expanded))
        use_batch = False
        if mode == "async_batch":
            use_batch = bool(spec.get("supports_batch"))
        elif mode == "auto":
            use_batch = bool(spec.get("supports_batch")) and len(expanded) >= int(
                cfg.get("auto_batch_min_items") or 20
            )
        estimate = _estimate_cost_usd(
            prompt_tokens=est_in,
            completion_tokens=est_out,
            model_key=model_key,
            batch_discount=use_batch,
        )
        log.info(
            "batch_llm_job_estimate",
            model_key=model_key,
            model_id=model_id,
            items=len(normalized),
            pending=len(pending),
            expanded=len(expanded),
            estimate_usd=round(estimate, 6),
            mode=mode,
            use_batch=use_batch,
        )
        if budget is not None and estimate > float(budget):
            raise BudgetExceeded(estimate, float(budget))

        if use_batch:
            # Async batch path — submit one OpenRouter batch and poll briefly.
            # For long jobs, callers can poll fetch_batch themselves later.
            result_map, failed, stats = await _run_async_batch(
                client=client,
                store=store,
                items=expanded,
                model_key=model_key,
                model_id=model_id,
                prompt_template=prompt_template,
                expected_out=expected_out,
                use_cache=use_cache and bool(spec.get("supports_prompt_cache")),
                max_retries=max_retries,
                validate=validate,
                chunk_parents=chunk_parents,
            )
        else:
            result_map, failed, stats = await _run_realtime(
                client=client,
                store=store,
                limiter=limiter,
                items=expanded,
                model_key=model_key,
                model_id=model_id,
                prompt_template=prompt_template,
                expected_out=expected_out,
                concurrency_base=concurrency_base,
                max_retries=max_retries,
                use_cache=use_cache and bool(spec.get("supports_prompt_cache")),
                validate=validate,
                chunk_parents=chunk_parents,
                max_out_cap=max_out,
            )

        # Merge previously completed
        for item_id, value in store.all_results().items():
            result_map.setdefault(item_id, value)

        # Reassemble chunked parents
        parents: dict[str, list[tuple[int, Any]]] = {}
        for cid, parent in chunk_parents.items():
            if cid in result_map:
                idx = int(cid.rsplit("#chunk", 1)[-1])
                parents.setdefault(parent, []).append((idx, result_map.pop(cid)))
        for parent, parts in parents.items():
            parts.sort(key=lambda x: x[0])
            # If values are strings, join; else keep list
            vals = [v for _, v in parts]
            if all(isinstance(v, str) for v in vals):
                result_map[parent] = "\n".join(vals)
            else:
                result_map[parent] = vals
            store.save_result(parent, result_map[parent])

        one_by_one = _estimate_cost_usd(
            prompt_tokens=est_in + estimate_tokens(system_est, tokenizer) * max(0, len(expanded) - 1),
            completion_tokens=est_out,
            model_key=model_key,
            batch_discount=False,
        )
        summary = {
            "model_key": model_key,
            "model_id": model_id,
            "items_total": len(normalized),
            "items_processed": len(result_map),
            "items_failed": len(failed),
            "batches_sent": stats.get("batches_sent", 0),
            "retries": stats.get("retries", 0),
            "prompt_tokens": stats.get("prompt_tokens", 0),
            "completion_tokens": stats.get("completion_tokens", 0),
            "estimated_cost_usd": round(estimate, 6),
            "actual_cost_usd": round(stats.get("actual_cost_usd", 0.0), 6),
            "cost_saved_vs_one_by_one_usd": round(
                max(0.0, one_by_one - stats.get("actual_cost_usd", 0.0)), 6
            ),
            "mode": "async_batch" if use_batch else "realtime",
        }
        log.info("batch_llm_job_complete", **summary)
        return BatchResult(results=result_map, failed=failed, summary=summary)
    finally:
        store.close()


async def _run_realtime(
    *,
    client: LLMAdapter,
    store: CheckpointStore,
    limiter: RateLimitState,
    items: list[Item],
    model_key: str,
    model_id: str,
    prompt_template: str | Callable,
    expected_out: int,
    concurrency_base: int,
    max_retries: int,
    use_cache: bool,
    validate: Callable | None,
    chunk_parents: dict[str, str],
    max_out_cap: int,
) -> tuple[dict[str, Any], list[DeadLetterEntry], dict[str, Any]]:
    if isinstance(prompt_template, str):
        system = prompt_template
        format_overhead = build_batch_user_prompt([], instruction="")[:200]
    else:
        system, _ = prompt_template(items[:1] or [Item(id="_", text="")])
        format_overhead = ""

    batches = pack_batches(
        items,
        model_key=model_key,
        system=system if isinstance(prompt_template, str) else system,
        format_overhead=format_overhead,
        expected_output_tokens_per_item=expected_out,
    )

    stats = {
        "batches_sent": 0,
        "retries": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "actual_cost_usd": 0.0,
    }
    results: dict[str, Any] = {}
    failed: list[DeadLetterEntry] = []
    pending_batches: list[PackedBatch] = list(batches)
    lock = asyncio.Lock()

    async def handle(batch: PackedBatch) -> None:
        requeue: list[PackedBatch] = []

        class _Requeue:
            async def put(self, item: PackedBatch) -> None:
                requeue.append(item)

        await _process_one_batch(
            batch=batch,
            client=client,
            store=store,
            limiter=limiter,
            model_key=model_key,
            model_id=model_id,
            prompt_template=prompt_template,
            expected_out=expected_out,
            max_retries=max_retries,
            use_cache=use_cache,
            validate=validate,
            results=results,
            failed=failed,
            stats=stats,
            requeue=_Requeue(),  # type: ignore[arg-type]
            max_out_cap=max_out_cap,
        )
        if requeue:
            async with lock:
                pending_batches.extend(requeue)

    while pending_batches:
        wave = list(pending_batches)
        pending_batches.clear()
        conc = limiter.suggested_concurrency(concurrency_base)
        sem = asyncio.Semaphore(conc)

        async def run_one(b: PackedBatch) -> None:
            async with sem:
                await handle(b)

        await asyncio.gather(*(run_one(b) for b in wave))

    return results, failed, stats


async def _process_one_batch(
    *,
    batch: PackedBatch,
    client: LLMAdapter,
    store: CheckpointStore,
    limiter: RateLimitState,
    model_key: str,
    model_id: str,
    prompt_template: str | Callable,
    expected_out: int,
    max_retries: int,
    use_cache: bool,
    validate: Callable | None,
    results: dict[str, Any],
    failed: list[DeadLetterEntry],
    stats: dict[str, Any],
    requeue: Any,
    max_out_cap: int,
) -> None:
    # Skip already done
    remaining = [it for it in batch.items if it.id not in store.completed_ids()]
    if not remaining:
        return

    if isinstance(prompt_template, str):
        system = prompt_template
        user = build_batch_user_prompt(
            remaining, instruction="Process each item. Return JSON keyed by id."
        )
    else:
        system, user = prompt_template(remaining)

    max_tokens = min(
        max_out_cap,
        max(256, batch.reserved_output_tokens or expected_out * len(remaining)),
    )
    attempt = 0
    while attempt <= max_retries:
        await limiter.wait_if_needed()
        resp = await client.complete(
            model=model_id,
            system=system,
            user=user,
            max_tokens=max_tokens,
            use_prompt_cache=use_cache,
        )
        stats["batches_sent"] += 1
        if resp.prompt_tokens:
            stats["prompt_tokens"] += resp.prompt_tokens
        if resp.completion_tokens:
            stats["completion_tokens"] += resp.completion_tokens
        if resp.prompt_tokens is not None or resp.completion_tokens is not None:
            stats["actual_cost_usd"] += _estimate_cost_usd(
                prompt_tokens=int(resp.prompt_tokens or 0),
                completion_tokens=int(resp.completion_tokens or 0),
                model_key=model_key,
                batch_discount=False,
            )
        limiter.note_request(
            prompt_tokens=int(resp.prompt_tokens or 0),
            completion_tokens=int(resp.completion_tokens or 0),
        )

        if resp.error:
            if resp.retryable and attempt < max_retries:
                stats["retries"] += 1
                if resp.status_code == 429:
                    limiter.note_rate_limit(resp.retry_after)
                await _sleep_backoff(attempt, resp.retry_after)
                attempt += 1
                continue
            for it in remaining:
                entry = DeadLetterEntry(
                    item_id=it.id,
                    error=str(resp.error),
                    attempts=attempt + 1,
                    payload=it.payload,
                )
                store.save_dead_letter(entry)
                failed.append(entry)
            return

        try:
            parsed = _parse_results_json(resp.text)
        except Exception as exc:  # noqa: BLE001
            if attempt < max_retries:
                stats["retries"] += 1
                # Truncation or parse fail → split batch
                if resp.truncated or len(remaining) > 1:
                    await _split_and_requeue(remaining, requeue)
                    return
                await _sleep_backoff(attempt)
                attempt += 1
                continue
            for it in remaining:
                entry = DeadLetterEntry(
                    item_id=it.id,
                    error=f"parse_failed:{type(exc).__name__}",
                    attempts=attempt + 1,
                    payload=it.payload,
                )
                store.save_dead_letter(entry)
                failed.append(entry)
            return

        if resp.truncated:
            # Keep valid ids; requeue missing via half-split
            missing = [it for it in remaining if it.id not in parsed]
            _accept_parsed(
                parsed,
                remaining,
                store=store,
                results=results,
                validate=validate,
                failed=failed,
            )
            if missing:
                if len(missing) == 1 and attempt < max_retries:
                    stats["retries"] += 1
                    max_tokens = min(max_out_cap, max_tokens * 2)
                    remaining = missing
                    attempt += 1
                    continue
                await _split_and_requeue(missing, requeue)
            return

        missing_ids = [it.id for it in remaining if it.id not in parsed]
        _accept_parsed(
            parsed,
            remaining,
            store=store,
            results=results,
            validate=validate,
            failed=failed,
        )
        if missing_ids:
            missing_items = [it for it in remaining if it.id in missing_ids]
            if len(missing_items) == 1 and attempt < max_retries:
                stats["retries"] += 1
                remaining = missing_items
                attempt += 1
                continue
            if len(missing_items) > 1:
                await _split_and_requeue(missing_items, requeue)
                return
            for it in missing_items:
                entry = DeadLetterEntry(
                    item_id=it.id,
                    error="missing_from_response",
                    attempts=attempt + 1,
                    payload=it.payload,
                )
                store.save_dead_letter(entry)
                failed.append(entry)
        return


def _accept_parsed(
    parsed: dict[str, Any],
    remaining: list[Item],
    *,
    store: CheckpointStore,
    results: dict[str, Any],
    validate: Callable | None,
    failed: list[DeadLetterEntry],
) -> None:
    wanted = {it.id: it for it in remaining}
    for item_id, value in parsed.items():
        if item_id not in wanted:
            continue
        if validate is not None:
            try:
                ok = bool(validate(item_id, value))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                continue
        store.save_result(item_id, value)
        results[item_id] = value


async def _split_and_requeue(items: list[Item], queue: asyncio.Queue) -> None:
    if len(items) <= 1:
        await queue.put(
            PackedBatch(items=items, input_tokens=0, reserved_output_tokens=0)
        )
        return
    mid = len(items) // 2
    await queue.put(
        PackedBatch(items=items[:mid], input_tokens=0, reserved_output_tokens=0)
    )
    await queue.put(
        PackedBatch(items=items[mid:], input_tokens=0, reserved_output_tokens=0)
    )


async def _run_async_batch(
    *,
    client: LLMAdapter,
    store: CheckpointStore,
    items: list[Item],
    model_key: str,
    model_id: str,
    prompt_template: str | Callable,
    expected_out: int,
    use_cache: bool,
    max_retries: int,
    validate: Callable | None,
    chunk_parents: dict[str, str],
) -> tuple[dict[str, Any], list[DeadLetterEntry], dict[str, Any]]:
    """Submit OpenRouter Batch API job; fall back to realtime if unsupported."""
    _ = chunk_parents
    stats = {
        "batches_sent": 0,
        "retries": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "actual_cost_usd": 0.0,
    }
    results: dict[str, Any] = {}
    failed: list[DeadLetterEntry] = []

    if isinstance(prompt_template, str):
        system = prompt_template
        user = build_batch_user_prompt(
            items, instruction="Process each item. Return JSON keyed by id."
        )
    else:
        system, user = prompt_template(items)

    requests = [
        {
            "custom_id": "job-1",
            "body": {
                "max_tokens": min(
                    int(model_spec(model_key).get("max_output_tokens") or 4096),
                    max(256, expected_out * len(items)),
                ),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        }
    ]
    try:
        submitted = await client.submit_batch(model=model_id, requests=requests)
        stats["batches_sent"] += 1
    except NotImplementedError:
        # Fall back
        limiter = RateLimitState()
        return await _run_realtime(
            client=client,
            store=store,
            limiter=limiter,
            items=items,
            model_key=model_key,
            model_id=model_id,
            prompt_template=prompt_template,
            expected_out=expected_out,
            concurrency_base=int(defaults().get("max_concurrency") or 4),
            max_retries=max_retries,
            use_cache=use_cache,
            validate=validate,
            chunk_parents={},
            max_out_cap=int(model_spec(model_key).get("max_output_tokens") or 4096),
        )
    except Exception as exc:  # noqa: BLE001
        for it in items:
            entry = DeadLetterEntry(
                item_id=it.id,
                error=f"batch_submit:{type(exc).__name__}",
                attempts=1,
                payload=it.payload,
            )
            store.save_dead_letter(entry)
            failed.append(entry)
        return results, failed, stats

    batch_id = str(
        submitted.get("id")
        or (submitted.get("data") or {}).get("id")
        or ""
    )
    if not batch_id:
        for it in items:
            entry = DeadLetterEntry(
                item_id=it.id,
                error="batch_missing_id",
                attempts=1,
                payload=it.payload,
            )
            store.save_dead_letter(entry)
            failed.append(entry)
        return results, failed, stats

    # Poll up to ~2 minutes for small jobs; longer jobs leave results for resume
    for _ in range(24):
        await asyncio.sleep(5)
        try:
            status = await client.fetch_batch(batch_id)
        except Exception:  # noqa: BLE001
            continue
        state = str(
            status.get("status")
            or (status.get("data") or {}).get("status")
            or ""
        ).lower()
        if state in {"completed", "complete", "succeeded", "success"}:
            # Best-effort extract inline output
            output = status.get("results") or status.get("output") or status
            text = ""
            if isinstance(output, dict):
                text = json.dumps(output)
            else:
                text = str(output)
            try:
                parsed = _parse_results_json(text)
            except Exception:  # noqa: BLE001
                # Try nested choices content
                try:
                    nested = status
                    choices = (
                        ((nested.get("results") or [{}])[0] or {})
                        .get("response", {})
                        .get("choices")
                        or []
                    )
                    msg = (choices[0] or {}).get("message", {}) if choices else {}
                    parsed = _parse_results_json(str(msg.get("content") or ""))
                except Exception as exc:  # noqa: BLE001
                    for it in items:
                        entry = DeadLetterEntry(
                            item_id=it.id,
                            error=f"batch_parse:{type(exc).__name__}",
                            attempts=1,
                            payload=it.payload,
                        )
                        store.save_dead_letter(entry)
                        failed.append(entry)
                    return results, failed, stats
            _accept_parsed(
                parsed,
                items,
                store=store,
                results=results,
                validate=validate,
                failed=failed,
            )
            for it in items:
                if it.id not in results:
                    entry = DeadLetterEntry(
                        item_id=it.id,
                        error="missing_from_batch_response",
                        attempts=1,
                        payload=it.payload,
                    )
                    store.save_dead_letter(entry)
                    failed.append(entry)
            stats["actual_cost_usd"] += _estimate_cost_usd(
                prompt_tokens=sum(estimate_tokens(it.text) for it in items),
                completion_tokens=expected_out * len(items),
                model_key=model_key,
                batch_discount=True,
            )
            return results, failed, stats
        if state in {"failed", "cancelled", "canceled", "expired"}:
            for it in items:
                entry = DeadLetterEntry(
                    item_id=it.id,
                    error=f"batch_{state}",
                    attempts=1,
                    payload=it.payload,
                )
                store.save_dead_letter(entry)
                failed.append(entry)
            return results, failed, stats

    # Still running — checkpoint nothing new; surface as pending failures for visibility
    for it in items:
        if it.id in store.completed_ids():
            continue
        entry = DeadLetterEntry(
            item_id=it.id,
            error=f"batch_pending:{batch_id}",
            attempts=1,
            payload={"batch_id": batch_id, "payload": it.payload},
        )
        store.save_dead_letter(entry)
        failed.append(entry)
    return results, failed, stats
