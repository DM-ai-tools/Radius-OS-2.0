"""Token counting and first-fit-decreasing batch packing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.integrations.batch_llm.limits import defaults, model_spec


@dataclass
class Item:
    id: str
    text: str
    payload: Any = None
    token_count: int = 0


@dataclass
class PackedBatch:
    items: list[Item]
    input_tokens: int
    reserved_output_tokens: int


@dataclass
class SplitChunk:
    parent_id: str
    chunk_index: int
    chunk_count: int
    text: str
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = f"{self.parent_id}#chunk{self.chunk_index}"


def estimate_tokens(text: str, tokenizer: str = "gemini_approx") -> int:
    """Best-effort token estimate. Prefer API usage calibration in production."""
    raw = text or ""
    if not raw:
        return 0
    name = (tokenizer or "").lower()
    if name in ("tiktoken_o200k", "openai"):
        try:
            import tiktoken  # type: ignore

            enc = tiktoken.get_encoding("o200k_base")
            return max(1, len(enc.encode(raw)))
        except Exception:  # noqa: BLE001
            pass
    # Anthropic / Gemini / calibrated fallback: ~4 chars per token for English prose
    return max(1, (len(raw) + 3) // 4)


def count_item_tokens(item_text: str, *, system: str, overhead: str, tokenizer: str) -> int:
    return estimate_tokens(system, tokenizer) + estimate_tokens(
        overhead + item_text, tokenizer
    )


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def split_oversized(
    item: Item,
    *,
    max_item_tokens: int,
    tokenizer: str,
    overlap_tokens: int | None = None,
) -> list[SplitChunk]:
    """Split one oversized item at paragraph/sentence boundaries with overlap."""
    cfg = defaults()
    overlap = int(overlap_tokens if overlap_tokens is not None else cfg.get("overlap_tokens", 80))
    text = item.text or ""
    if estimate_tokens(text, tokenizer) <= max_item_tokens:
        return [SplitChunk(parent_id=item.id, chunk_index=0, chunk_count=1, text=text)]

    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    if not parts:
        # Hard slice by characters as last resort
        chars = max(200, max_item_tokens * 4)
        pieces = [text[i : i + chars] for i in range(0, len(text), chars)]
        return [
            SplitChunk(parent_id=item.id, chunk_index=i, chunk_count=len(pieces), text=p)
            for i, p in enumerate(pieces)
        ]

    chunks: list[str] = []
    buf = ""
    for part in parts:
        candidate = f"{buf} {part}".strip() if buf else part
        if estimate_tokens(candidate, tokenizer) <= max_item_tokens:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        # overlap: take trailing tokens from previous buffer
        if overlap > 0 and chunks:
            prev = chunks[-1]
            approx_chars = overlap * 4
            prefix = prev[-approx_chars:] if len(prev) > approx_chars else prev
            buf = f"{prefix} {part}".strip()
            if estimate_tokens(buf, tokenizer) > max_item_tokens:
                buf = part
        else:
            buf = part
        if estimate_tokens(buf, tokenizer) > max_item_tokens:
            # still too big — hard split this part
            chars = max(200, max_item_tokens * 4)
            for i in range(0, len(buf), chars):
                chunks.append(buf[i : i + chars])
            buf = ""
    if buf:
        chunks.append(buf)

    return [
        SplitChunk(parent_id=item.id, chunk_index=i, chunk_count=len(chunks), text=c)
        for i, c in enumerate(chunks)
    ]


def pack_batches(
    items: list[Item],
    *,
    model_key: str,
    system: str,
    format_overhead: str,
    expected_output_tokens_per_item: int,
    safety_margin: float | None = None,
) -> list[PackedBatch]:
    """First-fit decreasing packing under context + output reservation."""
    spec = model_spec(model_key)
    cfg = defaults()
    margin = float(
        safety_margin if safety_margin is not None else cfg.get("safety_margin", 0.12)
    )
    tokenizer = str(spec.get("tokenizer") or "gemini_approx")
    context = int(spec.get("context_window") or 128000)
    max_out_cap = int(spec.get("max_output_tokens") or 4096)

    fixed = estimate_tokens(system, tokenizer) + estimate_tokens(format_overhead, tokenizer)
    for it in items:
        it.token_count = estimate_tokens(it.text or "", tokenizer)

    # Largest first
    ordered = sorted(items, key=lambda x: x.token_count, reverse=True)
    batches: list[PackedBatch] = []
    open_items: list[Item] = []
    open_in = 0

    def flush() -> None:
        nonlocal open_items, open_in
        if not open_items:
            return
        n = len(open_items)
        reserved = int(
            expected_output_tokens_per_item * n * (1.0 + margin)
        )
        reserved = min(reserved, max_out_cap)
        batches.append(
            PackedBatch(
                items=list(open_items),
                input_tokens=fixed + open_in,
                reserved_output_tokens=reserved,
            )
        )
        open_items = []
        open_in = 0

    for it in ordered:
        n = len(open_items) + 1
        reserved = int(expected_output_tokens_per_item * n * (1.0 + margin))
        reserved = min(reserved, max_out_cap)
        projected_in = fixed + open_in + it.token_count
        if projected_in + reserved > context and open_items:
            flush()
            n = 1
            reserved = int(expected_output_tokens_per_item * n * (1.0 + margin))
            reserved = min(reserved, max_out_cap)
            projected_in = fixed + it.token_count
        if projected_in + reserved > context:
            # Item alone is too large for packer — caller should split first
            flush()
            batches.append(
                PackedBatch(
                    items=[it],
                    input_tokens=fixed + it.token_count,
                    reserved_output_tokens=min(
                        int(expected_output_tokens_per_item * (1.0 + margin)),
                        max_out_cap,
                    ),
                )
            )
            continue
        open_items.append(it)
        open_in += it.token_count

    flush()
    return batches


def build_batch_user_prompt(
    items: list[Item],
    *,
    instruction: str,
    render_item: Callable[[Item], str] | None = None,
) -> str:
    """Ask the model for JSON keyed by item id."""
    lines = [
        instruction.strip(),
        "",
        "Return ONLY valid JSON of the form:",
        '{"results": {"<item_id>": <value>, ...}}',
        "Every item_id listed below must appear exactly once.",
        "",
        "Items:",
    ]
    for it in items:
        body = render_item(it) if render_item else (it.text or "")
        lines.append(f"### id={it.id}\n{body}")
    return "\n".join(lines)
