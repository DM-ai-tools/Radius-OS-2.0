"""Post-clustering LLM coherence gate — fail closed."""

from __future__ import annotations

from typing import Any

import pytest

from app.services import cluster_validation_gate as gate
from app.services.cluster_validation_gate import validate_clusters_coherence
from app.services.keyword_clustering import clusters_for_cdp


@pytest.fixture(autouse=True)
def _no_shared_cache(monkeypatch):
    async def _get_many(keys):
        return {}

    async def _set_many(values, ttl):
        return None

    monkeypatch.setattr(gate, "cache_get_many", _get_many)
    monkeypatch.setattr(gate, "cache_set_many", _set_many)


CONTEXT = dict(
    client_name="Northside Dental",
    primary_url="https://northsidedental.com.au",
    industry="Dental",
    services=["Teeth Whitening", "Dental Implants"],
    cdd_keywords=["dentist melbourne"],
    geography="Melbourne",
    client_id="client-1",
)


def _cluster(primary: str, *members: str, name: str | None = None) -> dict[str, Any]:
    kws = [{"keyword": primary, "role": "Primary", "intent": "commercial"}]
    for m in members:
        kws.append({"keyword": m, "role": "Secondary", "intent": "commercial"})
    return {
        "name": name or primary,
        "primary_keyword": primary,
        "intent": "commercial",
        "content_type": "service",
        "keyword_count": len(kws),
        "keywords": kws,
    }


def _caller(by_id: dict[int, dict[str, Any]], *, calls: list | None = None):
    async def _call(system: str, user: str) -> dict[str, Any]:
        if calls is not None:
            calls.append((system, user))
        return {
            "verdicts": [
                {"id": cid, **payload} for cid, payload in by_id.items()
            ]
        }

    return _call


async def test_keep_above_threshold_and_drop_rejected():
    clusters = [
        _cluster("teeth whitening cost", "professional teeth whitening"),
        _cluster("google ads management", "facebook ads agency"),
    ]
    caller = _caller(
        {
            1: {"verdict": "keep", "score": 0.9, "reason": "coherent whitening"},
            2: {"verdict": "drop", "score": 0.1, "reason": "platform mismatch"},
        }
    )
    kept, rejected, audit = await validate_clusters_coherence(
        clusters, caller=caller, **CONTEXT
    )
    assert len(kept) == 1
    assert kept[0]["primary_keyword"] == "teeth whitening cost"
    assert kept[0]["cluster_coherence_ok"] is True
    assert len(rejected) == 1
    assert rejected[0]["cluster_coherence_ok"] is False
    assert rejected[0]["relevance_unverified"] is True
    assert audit["kept"] == 1
    assert audit["dropped"] == 1


async def test_keep_below_threshold_is_dropped():
    caller = _caller({1: {"verdict": "keep", "score": 0.3, "reason": "weak"}})
    kept, rejected, _audit = await validate_clusters_coherence(
        [_cluster("maybe topic", "unrelated jobs")], caller=caller, **CONTEXT
    )
    assert kept == []
    assert len(rejected) == 1
    assert rejected[0]["cluster_sense_cause"] == "llm_drop"


async def test_review_and_missing_verdict_quarantine():
    caller = _caller({1: {"verdict": "review", "score": 0.5, "reason": "ambiguous"}})

    async def partial(system: str, user: str) -> dict[str, Any]:
        return {"verdicts": [{"id": 1, "verdict": "keep", "score": 0.9, "reason": "ok"}]}

    kept, rejected, audit = await validate_clusters_coherence(
        [_cluster("a", "b"), _cluster("c", "d")],
        caller=partial,
        **CONTEXT,
    )
    # id 2 missing → quarantine
    assert len(kept) == 1
    assert len(rejected) == 1
    assert rejected[0]["cluster_sense_cause"] == "missing_verdict"
    assert audit["quarantined"] == 1

    kept2, rejected2, audit2 = await validate_clusters_coherence(
        [_cluster("ambiguous topic", "maybe")],
        caller=caller,
        **CONTEXT,
    )
    assert kept2 == []
    assert len(rejected2) == 1
    assert audit2["quarantined"] == 1


async def test_no_provider_fails_closed(monkeypatch):
    monkeypatch.setattr(gate, "_provider_ready", lambda: False)
    kept, rejected, audit = await validate_clusters_coherence(
        [_cluster("teeth whitening")], **CONTEXT
    )
    assert kept == []
    assert len(rejected) == 1
    assert rejected[0]["cluster_sense_cause"] == "no_provider"
    assert audit["skipped"] == "no_provider"


async def test_disabled_passes_through(monkeypatch):
    clusters = [_cluster("teeth whitening cost", "whitening gel")]
    kept, rejected, audit = await validate_clusters_coherence(
        clusters, enabled=False, **CONTEXT
    )
    assert len(kept) == 1
    assert rejected == []
    assert audit["skipped"] == "disabled"


async def test_rejected_clusters_excluded_from_cdp():
    report = {
        "clusters": [
            {
                **_cluster("keep me", "keep member"),
                "cluster_coherence_ok": True,
                "cluster_sense_verdict": "keep",
            },
            {
                **_cluster("drop me", "noise"),
                "cluster_coherence_ok": False,
                "cluster_sense_verdict": "drop",
                "relevance_unverified": True,
            },
        ]
    }
    names = [c["name"] for c in clusters_for_cdp(report)]
    assert names == ["keep me"]


async def test_all_drop_guard_quarantines_batch():
    clusters = [
        _cluster("a1", "a2"),
        _cluster("b1", "b2"),
        _cluster("c1", "c2"),
    ]
    caller = _caller(
        {
            1: {"verdict": "drop", "score": 0.1, "reason": "x"},
            2: {"verdict": "drop", "score": 0.1, "reason": "y"},
            3: {"verdict": "drop", "score": 0.1, "reason": "z"},
        }
    )
    kept, rejected, audit = await validate_clusters_coherence(
        clusters, caller=caller, **CONTEXT
    )
    assert kept == []
    assert len(rejected) == 3
    assert audit["quarantined"] == 3
    assert any("all_drop_guard" in e for e in audit["errors"])
