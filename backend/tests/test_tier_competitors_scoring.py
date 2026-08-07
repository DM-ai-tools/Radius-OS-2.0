from app.ml.tier_competitors import PARAMS, derived_scores


def test_future_threat_uses_six_distinct_params_not_four():
    """Every PARAMS key set to a distinct value; if growth_indicators or
    digital_presence is counted twice, future_threat won't respond to a
    change in brand_authority (which isn't in the formula today)."""
    param_keys = [k for k, _, _ in PARAMS]
    comp_scores = {key: {"label": key, "score": 5} for key in param_keys}
    client_scores = {key: {"label": key, "score": 5} for key in param_keys}

    baseline = derived_scores(client_scores, comp_scores)

    bumped_scores = {**comp_scores, "brand_authority": {"label": "brand_authority", "score": 10}}
    bumped = derived_scores(client_scores, bumped_scores)

    assert bumped["future_threat"] != baseline["future_threat"]


def test_future_threat_does_not_double_count_growth_indicators():
    """Bumping growth_indicators alone should move future_threat by exactly
    its intended 0.30 weight (* 10), not 0.40 if it were still double-counted."""
    param_keys = [k for k, _, _ in PARAMS]
    comp_scores = {key: {"label": key, "score": 5} for key in param_keys}
    client_scores = {key: {"label": key, "score": 5} for key in param_keys}

    baseline = derived_scores(client_scores, comp_scores)

    bumped_scores = {**comp_scores, "growth_indicators": {"label": "growth_indicators", "score": 6}}
    bumped = derived_scores(client_scores, bumped_scores)

    delta = round(bumped["future_threat"] - baseline["future_threat"], 2)
    assert delta == 3.0  # (6 - 5) * 0.30 weight * 10 scale
