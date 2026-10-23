"""Answering the agent's questions on the user's behalf."""

from __future__ import annotations

from typing import Any

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger

log = get_logger("site_preview.answer")

MAX_MODEL_ANSWERS = 8

DEFAULT_ANSWER = (
    "Please decide yourself by following brief.md: use the draft's words exactly, take the "
    "design from the client's own site, never invent content, and do not deploy or publish "
    "anything. Deliver the self-contained index.html when you are done."
)

_RULES = """You answer questions from a design agent on behalf of the user.
Rules:
- Answer decisively in at most 120 words. Never ask a question back.
- Pick what keeps the page closest to the client's own site design while using the
  approved draft's words exactly as written.
- When given options, name the one you choose exactly as written.
- Facts you do not have come only from the client's website, or are left out.
- Never approve deploying, publishing, sending emails, purchases, connecting accounts
  or sharing credentials.
- If asked whether to continue, say yes, and finish the page and the visual check.
Return JSON: {"answer": "..."}"""


async def answer_question(
    question: str,
    *,
    options: list[str] | None,
    history: list[dict[str, Any]],
    context: str,
    answers_used: int,
) -> tuple[str, bool]:
    """``(answer, from_model)``. Falls back to a default answer after the model budget is
    spent or when the model fails; with options, the fallback picks the first one."""
    fallback = (f"{options[0]}. " if options else "") + DEFAULT_ANSWER
    if answers_used >= MAX_MODEL_ANSWERS:
        return fallback, False
    past = "\n".join(f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in history[-6:])
    user = (
        f"The job:\n{context}\n\n"
        + (f"Earlier questions and answers:\n{past}\n\n" if past else "")
        + f"The agent asks:\n{question}\n"
        + (f"\nOptions: {', '.join(options)}\n" if options else "")
    )
    try:
        reply = await synthesize_json(_RULES, user, max_tokens=1024, raise_on_error=True)
        answer = str((reply or {}).get("answer") or "").strip()
    except Exception as exc:  # noqa: BLE001
        # A failed answer must not stall the agent; the default answer goes instead.
        log.warning("preview_answer_failed", error=str(exc)[:200])
        answer = ""
    if not answer:
        return fallback, False
    return answer[:1200], True
