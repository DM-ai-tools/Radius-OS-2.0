from app.integrations.llm import _extract_openrouter_text, _gemini_reasoning_budget


def test_extract_openrouter_text_string():
    assert _extract_openrouter_text({"content": '{"ok": true}'}) == '{"ok": true}'


def test_extract_openrouter_text_parts():
    message = {
        "content": [
            {"type": "text", "text": '{"competitors":['},
            {"type": "text", "text": "]}"},
        ]
    }
    assert _extract_openrouter_text(message) == '{"competitors":[]}'


def test_extract_openrouter_text_falls_back_to_reasoning():
    message = {"content": None, "reasoning": '{"ok": true}'}
    assert _extract_openrouter_text(message) == '{"ok": true}'


def test_gemini_reasoning_budget_leaves_answer_room():
    budget = _gemini_reasoning_budget(4096)
    assert budget == 2048
    assert budget < 4096
