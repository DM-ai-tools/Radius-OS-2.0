"""Verify ARG Finance-style YMYL clears when positioning is in shared memory."""
from app.services.content_brief import author_standing, refresh_brief_author_gates


def test_ymyl_accepts_commercial_positioning_as_credentials():
    marketing = {
        "client_intake": {"primary_contact_name": "Alex Broker"},
        "commercial_scope": {
            "positioning": (
                "Professional mortgage and finance broker with over 15 years "
                "in business and 20+ awards, offering commercial, business, "
                "home, SMSF and asset finance in Victoria."
            )
        },
    }
    standing = author_standing(
        "best commercial loans",
        industry="Mortgage and finance broking",
        marketing=marketing,
        client_name="ARG Finance",
    )
    assert standing["ymyl"] is True
    assert standing["standing"] == "recorded"
    assert standing["author"] == "Alex Broker"


def test_refresh_clears_stale_ymyl_blocker_on_cached_brief():
    marketing = {
        "client_intake": {"primary_contact_name": "Alex Broker"},
        "commercial_scope": {
            "positioning": "Licensed mortgage broker, 15 years, ASIC credit representative."
        },
    }
    stale = {
        "keyword": "best commercial loans",
        "url": "/blog/best-commercial-loans",
        "action": "create",
        "author": "Alex Broker",
        "differentiation": (
            "ARG Finance publishes from licensed broker practice with named "
            "Victoria delivery — not another restatement of generic loan listicles."
        ),
        "writer_ready": False,
        "preflight": {
            "ymyl": True,
            "author": "Alex Broker",
            "author_standing": "unverified",
            "blockers": [
                "YMYL-adjacent topic — no demonstrated credentials in shared memory. "
                "Do not send to a writer until an expert author is named."
            ],
        },
    }
    refreshed = refresh_brief_author_gates(
        stale,
        industry="Mortgage and finance broking",
        marketing=marketing,
        client_name="ARG Finance",
    )
    assert refreshed["preflight"]["ymyl"] is True
    assert refreshed["preflight"]["author_standing"] == "recorded"
    assert not any("YMYL" in str(b) for b in refreshed["preflight"]["blockers"])
    assert refreshed["writer_ready"] is True
