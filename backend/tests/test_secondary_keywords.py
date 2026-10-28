"""Secondary keywords on every URL-map row: short rows are topped up with measured modifier variants."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.integrations import dataforseo
from app.models import KeywordPoolFetch
from app.services import secondary_keywords as sk
from app.services import site_architecture
from app.services.keyword_relevance import build_relevance_context
from app.services.secondary_keywords import (
    PREFIX_MODIFIERS,
    SECONDARY_MODIFIERS,
    modifier_candidates,
    relevance_context_from_stored,
    top_up_secondary_keywords,
    top_up_url_map_secondaries,
)
from app.services.shared_keyword_pool.store import load_seed, save_seed

LOCATION = 2036
LANGUAGE = "en"

CONTEXT = build_relevance_context(
    services=["SEO", "Website design"],
    cdd_keywords=["seo", "web design"],
    industry="Digital marketing agency",
    geography="Melbourne, Australia",
    brand_name="Acme",
    domain="acme.example",
    seeds=["seo", "web design"],
)


# A client that sells the four head terms the SERP-format tests use.
HEADS_CONTEXT = build_relevance_context(
    services=["SEO", "Web design", "Emergency plumber", "CRM software"],
    cdd_keywords=["seo", "web design", "plumber", "crm software"],
    industry="Digital marketing agency",
    brand_name="Acme",
    domain="acme.example",
)


# A client with dental head terms as well as the web ones.
HEADS_CONTEXT_DENTAL = build_relevance_context(
    services=["SEO", "Dental implants", "Emergency dentist"],
    cdd_keywords=["seo", "dental implants", "emergency dentist"],
    industry="Dental clinic",
    brand_name="Acme",
    domain="acme.example",
)


class FakeVolumes:
    """Stands in for ``dataforseo.search_volume``: records each call, answers from a table.

    A keyword missing from the table is not returned at all (the provider has no data for
    it); a table value of ``None`` is returned with no volume.
    """

    def __init__(self, volumes: dict[str, int | None] | None = None, errors=None):
        self.volumes = dict(volumes or {})
        self.errors = list(errors or [])
        self.calls: list[dict] = []

    async def __call__(self, keywords, *, location_code, language_code):
        self.calls.append(
            {"keywords": list(keywords), "location_code": location_code, "language": language_code}
        )
        rows = [
            {"keyword": keyword, "volume": self.volumes[keyword], "cpc": 2.5}
            for keyword in keywords
            if keyword in self.volumes
        ]
        return rows, list(self.errors)

    @property
    def asked(self) -> list[str]:
        return [keyword for call in self.calls for keyword in call["keywords"]]


def _spy_log(monkeypatch) -> list[tuple[str, str, dict]]:
    """Replace the module's logger with one that records (level, event, fields)."""
    seen: list[tuple[str, str, dict]] = []

    class Spy:
        def __getattr__(self, level):
            return lambda event, **fields: seen.append((level, event, fields))

    monkeypatch.setattr(sk, "log", Spy())
    return seen


@pytest.fixture
def volumes(monkeypatch):
    def install(table=None, errors=None) -> FakeVolumes:
        fake = FakeVolumes(table, errors)
        monkeypatch.setattr(dataforseo, "search_volume", fake)
        return fake

    return install


def _row(primary: str, secondaries=(), **extra) -> dict:
    """A URL-map row the way ``build_final_url_map`` hands it over."""
    row = {
        "primary_keyword": primary,
        "secondary_keywords": list(secondaries),
        "secondary_keywords_sheet": list(secondaries),
        "is_home": False,
        "dedicated_url": True,
    }
    row.update(extra)
    return row


async def _top_up(entries, *, context=CONTEXT, **kwargs):
    kwargs.setdefault("location_code", LOCATION)
    kwargs.setdefault("country_location_code", LOCATION)
    return await top_up_secondary_keywords(
        entries, language_code=LANGUAGE, relevance_context=context, **kwargs
    )


# ------------------------------------------------------------------ top-up behaviour


async def test_rows_below_target_are_topped_up_with_measured_modifier_variants(volumes):
    fake = volumes(
        {
            "best seo": 900,
            "seo company": 400,
            "seo agency": 150,
            "best web design": 300,
            "web design company": 120,
        }
    )
    seo = _row("seo", ["seo audit", "seo packages"])
    design = _row("web design")

    report = await _top_up([seo, design])

    assert seo["secondary_keywords_sheet"] == ["seo audit", "seo packages", "best seo", "seo company", "seo agency"]
    assert seo["secondary_keywords"] == seo["secondary_keywords_sheet"]
    assert design["secondary_keywords_sheet"] == ["best web design", "web design company"]
    assert design["secondary_keywords"] == ["best web design", "web design company"]
    # One batched call carries every candidate of the run.
    assert len(fake.calls) == 1
    assert "best seo" in fake.asked and "web design agency" in fake.asked
    assert fake.calls[0]["location_code"] == LOCATION and fake.calls[0]["language"] == LANGUAGE
    assert report["rows_topped_up"] == 2
    assert report["kept"] == 5
    assert report["candidates"] == len(set(fake.asked))
    assert report["empty_rows"] == 0


async def test_variants_without_measured_volume_are_dropped(volumes):
    volumes(
        {
            "seo company": 400,  # measured
            "best seo": None,  # returned, but no volume
            "seo agency": 5,  # below the floor of 10
            "seo firm": 0,
            # every other variant is absent from the provider's answer
        }
    )
    row = _row("seo")
    report = await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["seo company"]
    assert report["kept"] == 1


async def test_the_volume_floor_is_ten(volumes):
    volumes({"seo company": 10, "seo agency": 9})
    row = _row("seo")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["seo company"]


async def test_kept_variants_carry_no_label_or_extra_field(volumes):
    volumes({"best seo": 900, "seo company": 400})
    row = _row("seo", ["seo audit"], cluster="SEO", level=1)
    before_keys = set(row)

    await _top_up([row])

    assert row["secondary_keywords_sheet"] == ["seo audit", "best seo", "seo company"]
    # Plain strings in both lists, indistinguishable from the cluster's own keywords.
    for key in ("secondary_keywords", "secondary_keywords_sheet"):
        assert all(type(item) is str for item in row[key])
    # No origin flag, label or tag was added anywhere on the entry.
    assert set(row) == before_keys
    assert "suggested" not in str(row).lower() and "modifier" not in str(row).lower()


async def test_a_row_with_no_measured_variant_stays_empty(volumes):
    volumes({})
    row = _row("seo")
    report = await _top_up([row])
    assert row["secondary_keywords_sheet"] == []
    assert row["secondary_keywords"] == []
    assert report == {
        "rows_topped_up": 0,
        "candidates": 10,
        "candidates_dropped_by_cap": 0,
        "kept": 0,
        "empty_rows": 1,
        "lookup_errors": [],
    }


async def test_rows_already_at_target_are_untouched(volumes):
    fake = volumes({"best seo": 900})
    full = _row("seo", ["a seo", "b seo", "c seo", "d seo", "e seo"])
    snapshot = copy.deepcopy(full)

    report = await _top_up([full])

    assert full == snapshot
    assert fake.calls == []  # nothing to look up
    assert report["rows_topped_up"] == 0 and report["candidates"] == 0 and report["empty_rows"] == 0


async def test_only_rows_below_target_are_looked_up_when_the_map_is_mixed(volumes):
    fake = volumes({"best web design": 300})
    full = _row("seo", ["a seo", "b seo", "c seo", "d seo", "e seo"])
    short = _row("web design", ["web design cost"])
    await _top_up([full, short])
    assert all(keyword.endswith("web design") or keyword.startswith("web design") for keyword in fake.asked)
    assert short["secondary_keywords_sheet"] == ["web design cost", "best web design"]


async def test_the_target_and_the_candidate_cap_can_be_passed_in(volumes):
    fake = volumes({"best seo": 900})
    row = _row("seo", ["a seo", "b seo"])
    await _top_up([row], target=2, cap=5)  # two secondaries already meet a target of 2
    assert fake.calls == []
    await _top_up([row], target=3, cap=5)
    assert len(fake.asked) == 5 and row["secondary_keywords_sheet"][-1:] == ["best seo"]


async def test_a_row_is_filled_up_to_ten_and_no_further(volumes):
    table = {f"{m} seo": 1000 - i for i, m in enumerate(SECONDARY_MODIFIERS)}
    table.update({f"seo {m}": 500 - i for i, m in enumerate(SECONDARY_MODIFIERS)})
    volumes(table)
    own = ["seo audit", "seo cost", "seo packages", "seo pricing"]
    row = _row("seo", own)
    await _top_up([row])
    # Four of its own plus the six highest-volume variants (prefixes "best seo" and
    # suffixes "seo company" only: "company seo" is never asked); the other four are left out.
    assert row["secondary_keywords_sheet"] == [
        *own, "best seo", "top seo", "cheap seo", "affordable seo", "seo company", "seo agency",
    ]
    assert row["secondary_keywords"] == row["secondary_keywords_sheet"]


async def test_a_row_whose_sheet_column_is_empty_counts_its_secondary_keywords_list(volumes):
    volumes({"best seo": 900})
    # Reports read the sheet column and fall back to the secondary_keywords list.
    row = _row("seo")
    row["secondary_keywords"] = ["seo audit", "seo packages", "seo cost", "seo pricing", "seo reporting"]
    row["secondary_keywords_sheet"] = []
    snapshot = copy.deepcopy(row)
    await _top_up([row])
    assert row == snapshot


async def test_the_home_row_is_topped_up_like_any_row(volumes):
    volumes({"best digital marketing agency": 800})
    home = _row("digital marketing agency", ["marketing agency"], is_home=True)
    await _top_up([home])
    assert home["secondary_keywords_sheet"] == ["marketing agency", "best digital marketing agency"]


async def test_a_candidate_that_is_another_rows_primary_is_not_a_secondary(volumes):
    volumes({"seo services": 500, "seo company": 300, "best seo services": 100})
    seo = _row("seo")
    services = _row("seo services")
    await _top_up([seo, services])
    assert "seo services" not in seo["secondary_keywords_sheet"]
    assert seo["secondary_keywords_sheet"] == ["seo company"]
    assert services["secondary_keywords_sheet"] == ["best seo services"]


async def test_two_rows_never_claim_the_same_variant(volumes):
    # "best seo services" is a variant of "seo services" and of "best seo" + "services".
    volumes({"best seo services": 100})
    first = _row("seo services")
    second = _row("best seo")
    await _top_up([first, second], target=5)
    claimed = first["secondary_keywords_sheet"] + second["secondary_keywords_sheet"]
    assert claimed.count("best seo services") == 1


async def test_a_failed_lookup_leaves_rows_as_they_were(volumes, monkeypatch):
    async def boom(keywords, *, location_code, language_code):
        raise RuntimeError("provider down")

    monkeypatch.setattr(dataforseo, "search_volume", boom)
    row = _row("seo", ["seo audit"])
    report = await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["seo audit"]
    assert report["kept"] == 0 and report["lookup_errors"]


async def test_provider_errors_are_reported_and_rows_are_not_filled_with_guesses(volumes, monkeypatch):
    spy = _spy_log(monkeypatch)
    volumes({}, errors=["dataforseo_unavailable"])
    row = _row("seo")
    report = await _top_up([row])
    assert row["secondary_keywords_sheet"] == []
    assert report["lookup_errors"] == ["dataforseo_unavailable"]
    assert ("warning", "secondary_keywords_lookup_errors", {"errors": ["dataforseo_unavailable"]}) in spy


# --------------------------------------------------------------------- the filters


async def test_variants_that_fail_buyer_fit_are_dropped(volumes):
    # "best website builder" is someone shopping for a DIY tool; "website builder company" is a buyer.
    volumes({"best website builder": 800, "website builder company": 200})
    row = _row("website builder")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["website builder company"]


async def test_variants_a_client_page_must_not_target_are_dropped(volumes):
    # Do-it-yourself searches are rerouted to content, never kept on a service page.
    volumes({"cheap diy seo": 700, "diy seo company": 50})
    row = _row("diy seo")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == []


async def test_variants_that_fail_relevance_are_dropped(volumes):
    volumes({"best paralympics": 900, "paralympics company": 900})
    row = _row("paralympics")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == []


async def test_without_a_context_hard_rejects_and_buyer_fit_still_apply(volumes):
    volumes({"best website builder": 800, "website builder company": 200})
    row = _row("website builder")
    await _top_up([row], context=None)
    assert row["secondary_keywords_sheet"] == ["website builder company"]


# ---------------------------------------------------------------- candidate building


def test_modifiers_sit_in_their_natural_place():
    assert modifier_candidates("seo", []) == [
        "best seo",
        "top seo",
        "seo company",
        "seo agency",
        "seo firm",
        "seo services",
        "seo consultant",
        "seo provider",
        "cheap seo",
        "affordable seo",
    ]
    assert PREFIX_MODIFIERS == {"best", "top", "cheap", "affordable"}
    # The list the owner gave is unchanged; only where each word goes is decided.
    assert SECONDARY_MODIFIERS == (
        "best", "top", "company", "agency", "firm", "services", "consultant", "provider", "cheap", "affordable",
    )


@pytest.mark.parametrize("primary", ["seo", "dental implants", "plumber"])
def test_no_candidate_puts_a_suffix_word_in_front_or_a_prefix_word_behind(primary):
    candidates = modifier_candidates(primary, [])
    assert len(candidates) == 10
    prefixes = ("best", "top", "cheap", "affordable")
    suffixes = ("company", "agency", "firm", "services", "consultant", "provider")
    for candidate in candidates:
        assert candidate.startswith(tuple(f"{p} " for p in prefixes)) or candidate.endswith(
            tuple(f" {x}" for x in suffixes)
        )
        assert not candidate.startswith(tuple(f"{x} " for x in suffixes))
        assert not candidate.endswith(tuple(f" {p}" for p in prefixes))
    assert f"best {primary}" in candidates and f"{primary} company" in candidates
    assert f"agency {primary}" not in candidates and f"firm {primary}" not in candidates
    assert f"{primary} best" not in candidates and f"{primary} top" not in candidates


@pytest.mark.parametrize(
    "primary",
    [
        "digital marketing agency",
        "seo company",
        "seo companies",
        "accounting firm",
        "web design services",
        "seo service",
        "marketing consultants",
        "managed it provider",
    ],
)
def test_a_primary_that_names_a_provider_takes_no_suffix_modifier(primary):
    candidates = modifier_candidates(primary, [])
    assert candidates == [f"best {primary}", f"top {primary}", f"cheap {primary}", f"affordable {primary}"]
    assert "digital marketing agency company" not in candidates


def test_a_provider_word_must_be_a_whole_word_to_count():
    # "firmware" and "servicemen" are not firm / service.
    assert "firmware company" in modifier_candidates("firmware", [])
    assert "servicemen agency" in modifier_candidates("servicemen", [])


def test_candidates_skip_places_empty_primary_and_modifier_already_in_primary():
    assert modifier_candidates("", []) == []
    assert modifier_candidates("   ", []) == []
    # A place word in the primary keeps every candidate out.
    assert modifier_candidates("seo melbourne", []) == []
    assert modifier_candidates("seo fitzroy", [], places=["fitzroy"]) == []
    assert modifier_candidates("seo fitzroy", [])  # fitzroy is only a place for this client
    # A modifier that is a place (a custom list) is never built.
    assert modifier_candidates("seo", [], ["near me", "company"]) == ["seo company"]
    # The primary is never its own candidate; neither is a keyword the row already has.
    assert "seo" not in modifier_candidates("seo", [])
    assert "best seo" not in modifier_candidates("seo", ["Best  SEO"])
    # A modifier the primary already contains is skipped (plural or singular), whichever side it sits.
    best = modifier_candidates("best seo", [])
    assert not any(c.count("best") > 1 for c in best)
    assert "best seo best" not in best
    top = modifier_candidates("top seo tools", [])
    assert not any(c.count("top") > 1 for c in top)
    services = modifier_candidates("seo services", [])
    assert "best seo services" in services and "seo services services" not in services
    agencies = modifier_candidates("seo agencies", [])
    assert "seo agencies agency" not in agencies and "best seo agencies" in agencies
    cheaper = modifier_candidates("affordable seo", [])
    assert not any(c.count("affordable") > 1 for c in cheaper)


@pytest.mark.parametrize("primary", ["best seo", "top seo tools", "cheap web design", "affordable plumber"])
def test_prefix_modifiers_do_not_stack(primary):
    candidates = modifier_candidates(primary, [])
    # Nothing is put in front of a primary that already starts with a prefix word.
    assert all(c.startswith(f"{primary} ") for c in candidates)
    assert not any(c.startswith(("best ", "top ", "cheap ", "affordable ")) and not c.startswith(primary) for c in candidates)
    assert "top best seo" not in candidates and "best top seo tools" not in candidates
    # Its suffix candidates are untouched.
    assert f"{primary} company" in candidates


def test_a_prefix_word_inside_the_primary_does_not_stop_the_prefixes():
    # Only a leading prefix word counts: "seo best practices" is not "best seo".
    assert "top seo best practices" in modifier_candidates("seo best practices", [])


def test_a_candidate_the_provider_would_clean_to_other_text_is_not_built():
    # "&" is stripped before the lookup, so the volume that came back would belong to "seo ppc".
    assert modifier_candidates("seo & ppc", []) == []
    assert modifier_candidates("seo (australia)", []) == []
    assert modifier_candidates("seo/ppc", []) == []
    # Apostrophes and hyphens survive cleaning.
    assert "best o'brien e-commerce" in modifier_candidates("o'brien e-commerce", [])
    # The provider cuts a keyword at 80 characters; a longer candidate is not asked for.
    primary = " ".join(["a" * 30, "b" * 30, "c" * 10])  # 72 characters
    candidates = modifier_candidates(primary, [])
    assert f"best {primary}" in candidates  # 77
    assert f"{primary} consultant" not in candidates  # 83
    assert all(len(c) <= 80 for c in candidates)


async def test_lookup_cap_limits_candidates_to_the_cap(volumes):
    fake = volumes({})
    # 60 rows with nothing and 60 rows that have four secondaries each; 10 candidates per row.
    empty = [_row(f"empty{i} seo") for i in range(60)]
    short = [_row(f"short{i} design", [f"a{i} x", f"b{i} x", f"c{i} x", f"d{i} x"]) for i in range(60)]
    assert get_settings().keyword_secondary_lookup_cap == 1000
    assert get_settings().keyword_secondary_target == 5

    report = await _top_up([*short, *empty])

    assert len(fake.calls) == 1  # one batched call, and 1000 is one provider task
    asked = fake.calls[0]["keywords"]
    assert len(asked) == 1000 == report["candidates"]
    # The rows with the fewest secondary keywords come first, so the cap falls on the others.
    assert sum("empty" in keyword for keyword in asked) == 600
    assert sum("short" in keyword for keyword in asked) == 400
    assert report["candidates_dropped_by_cap"] == 200


async def test_a_sixty_row_map_fits_under_the_default_cap(volumes):
    fake = volumes({})
    report = await _top_up([_row(f"service{i} seo") for i in range(60)])
    assert len(fake.asked) == 600 and report["candidates_dropped_by_cap"] == 0


async def test_the_cap_serves_the_fewest_secondaries_first_and_supporting_rows_last(volumes):
    fake = volumes({})
    # Ten candidates each. Order for the cap: bravo (0), delta (1), alpha (3), then the
    # supporting row charlie (0, but it folds into another page, so it goes last).
    alpha = _row("alpha", ["a1", "a2", "a3"])
    bravo = _row("bravo")
    charlie = _row("charlie", dedicated_url=False)
    delta = _row("delta", ["d1"])

    report = await _top_up([alpha, bravo, charlie, delta], cap=25)

    asked = fake.asked
    assert len(asked) == 25 == report["candidates"]
    assert [k for k in asked if "bravo" in k] == modifier_candidates("bravo", [])
    assert [k for k in asked if "delta" in k] == modifier_candidates("delta", [])
    assert [k for k in asked if "alpha" in k] == modifier_candidates("alpha", [])[:5]
    assert not any("charlie" in k for k in asked)
    # alpha's other five and all ten of charlie's were left out.
    assert report["candidates_dropped_by_cap"] == 15


async def test_a_supporting_row_is_still_topped_up_when_there_is_room(volumes):
    volumes({"best web design": 90})
    supporting = _row("web design", dedicated_url=False)
    await _top_up([supporting])
    assert supporting["secondary_keywords_sheet"] == ["best web design"]


async def test_a_candidate_that_two_rows_share_is_dropped_once(volumes):
    fake = volumes({})
    # "best seo services" is a candidate of both rows; it takes one slot, and is counted once.
    report = await _top_up([_row("seo services"), _row("best seo")], cap=6)
    assert len(set(fake.asked)) == len(fake.asked)
    assert report["candidates"] == len(fake.asked) <= 6
    assert report["candidates_dropped_by_cap"] == len(
        {*modifier_candidates("seo services", ["best seo"]), *modifier_candidates("best seo", ["seo services"])}
        - set(fake.asked)
    )


async def test_a_capped_run_logs_a_warning_and_a_clean_run_does_not(volumes, monkeypatch):
    spy = _spy_log(monkeypatch)
    volumes({})
    await _top_up([_row("seo"), _row("web design")], cap=12)
    capped = [fields for level, event, fields in spy if event == "secondary_keywords_capped"]
    assert capped == [{"dropped": 8, "looked_up": 12, "cap": 12}]
    assert all(level == "warning" for level, event, _ in spy if event == "secondary_keywords_capped")

    del spy[:]
    await _top_up([_row("seo")])
    assert not any(event == "secondary_keywords_capped" for _, event, _ in spy)


async def test_a_smaller_cap_is_honoured(volumes):
    fake = volumes({})
    await _top_up([_row("seo"), _row("web design")], cap=7)
    assert len(fake.asked) == 7


# ------------------------------------------------- rows that target informational searches


@pytest.mark.parametrize(
    "fields",
    [
        # A comparison page type with nothing saying the row is a buyer's, or with an intent that is not.
        {"ia_page_type": "comparison"},
        {"search_intent": "informational", "ia_page_type": "comparison"},
        {"intent": "informational", "ia_page_type": "comparison"},
        {"search_intent": "navigational", "ia_page_type": "comparison"},
        # A brand (navigational) row with a content page type.
        {"search_intent": "navigational", "ia_page_type": "blog"},
        {"search_intent": "navigational", "ia_page_type": "guide"},
        {"search_intent": "navigational", "ia_page_type": "listicle"},
    ],
)
async def test_comparison_and_brand_rows_get_no_modifier_candidates(volumes, fields):
    fake = volumes({"best dental implants": 800, "dental implants company": 300})
    row = _row("dental implants", **fields)
    report = await _top_up([row])
    assert fake.calls == []
    assert row["secondary_keywords_sheet"] == [] and row["secondary_keywords"] == []
    assert report["candidates"] == 0
    assert report["empty_rows"] == 1  # it stays empty; nothing is invented for it


@pytest.mark.parametrize("primary", ["seo", "dental implants", "emergency dentist"])
@pytest.mark.parametrize("page_type", ["service", "blog", "listicle", "guide", "faq", "article", None])
@pytest.mark.parametrize("intent_field", ["search_intent", "intent"])
async def test_a_bare_head_term_labelled_informational_is_topped_up(volumes, primary, page_type, intent_field):
    # The intent heuristic labels plain service head terms informational (5 of 13 in the stored
    # 22 October data), and a SERP can hand them a blog or listicle page type. Neither is a
    # reason to leave the row empty: only a variant with no measured volume may do that.
    volumes({f"best {primary}": 800, f"{primary} company": 300})
    fields = {intent_field: "informational"}
    if page_type:
        fields["ia_page_type"] = page_type
    row = _row(primary, **fields)
    report = await _top_up([row], context=HEADS_CONTEXT_DENTAL)
    assert row["secondary_keywords_sheet"] == [f"best {primary}", f"{primary} company"]
    assert report["candidates"] > 0


@pytest.mark.parametrize(
    "primary",
    [
        # A question word first.
        "what is seo",
        "how to choose a dentist",
        "why is my website slow",
        "when to hire a plumber",
        "who is the best dentist",
        "where to buy dental implants",
        "which crm is best",
        "can seo improve sales",
        "does seo work",
        "is seo worth it",
        "are dental implants safe",
        "should i hire a seo agency",
        # An informational word anywhere.
        "seo checklist",
        "dental implant tips",
        "seo course",
        "seo guide",
        "seo guides for beginners",
        "seo tutorial",
        "seo tutorials",
        "website checklists",
        "seo template",
        "seo templates",
        "seo example",
        "seo examples",
        "seo meaning",
        "seo definition",
        "dental marketing ideas",
        "seo courses",
        "seo training",
    ],
)
@pytest.mark.parametrize("intent", ["commercial", "transactional", "informational"])
async def test_a_primary_with_an_informational_form_is_skipped_whatever_its_intent(volumes, primary, intent):
    fake = volumes({f"best {primary}": 800, f"{primary} company": 300})
    row = _row(primary, search_intent=intent, ia_page_type="service")
    report = await _top_up([row])
    assert fake.calls == [] and report["candidates"] == 0
    assert row["secondary_keywords_sheet"] == []


def test_an_informational_form_needs_a_whole_word():
    def wants(primary):
        return sk._wants_variants(_row(primary, search_intent="commercial", ia_page_type="service"))

    # A question word only counts as the first word; an informational word only as a whole word.
    assert wants("seo how to") is True and wants("island hotels") is True and wants("wholesale seo") is True
    assert wants("tipping point seo") is True and wants("coursework help") is True
    assert wants("training wheels") is False  # "training" is one of the words
    assert wants("trainings seo") is True
    assert wants("homeowners guide") is False
    assert wants("guided tours") is True and wants("exampled seo") is True


@pytest.mark.parametrize("page_type", ["service", "page", None])
async def test_a_brand_row_with_an_ordinary_page_type_is_still_topped_up(volumes, page_type):
    volumes({"best acme": 100})
    fields = {"ia_page_type": page_type} if page_type else {}
    row = _row("acme", search_intent="navigational", **fields)
    await _top_up([row], context=None)
    assert row["secondary_keywords_sheet"] == ["best acme"]


@pytest.mark.parametrize("primary", ["seo vs sem", "hubspot alternatives", "crm comparison"])
async def test_a_primary_that_is_itself_a_comparison_is_skipped_whatever_its_intent_or_page_type(volumes, primary):
    fake = volumes({f"best {primary}": 800, f"{primary} company": 300})
    for fields in (
        {"search_intent": "commercial", "ia_page_type": "service"},
        {"search_intent": "transactional", "ia_page_type": "comparison"},
    ):
        row = _row(primary, **fields)
        report = await _top_up([row])
        assert row["secondary_keywords_sheet"] == [] and report["candidates"] == 0
    assert fake.calls == []


@pytest.mark.parametrize(
    "primary",
    [
        "seo vs sem",
        "seo vs. sem",
        "seo versus ppc",
        "hubspot alternatives",
        "hubspot alternative",
        "crm comparison",
        "compare crm tools",
        "crm tools compared",
    ],
)
def test_a_comparison_word_in_the_primary_makes_the_row_a_comparison(primary):
    for fields in (
        {"search_intent": "commercial", "ia_page_type": "service"},
        {"search_intent": "transactional", "ia_page_type": "comparison"},
        {"search_intent": "commercial"},
        {},
    ):
        assert sk._wants_variants(_row(primary, **fields)) is False


def test_a_comparison_word_must_be_a_whole_word_of_the_primary():
    for primary in ("versatile seo", "svs software", "comparisons seo", "uncompared seo"):
        assert sk._wants_variants(_row(primary, search_intent="commercial", ia_page_type="service")) is True


@pytest.mark.parametrize("intent", ["commercial", "transactional"])
@pytest.mark.parametrize("primary", ["seo", "web design", "emergency plumber", "crm software"])
async def test_a_head_term_the_serp_turned_into_a_comparison_page_is_topped_up(volumes, primary, intent):
    # One "X vs Y" title in the SERP makes ``dominant_format`` "comparison", and the map row's
    # page type follows. The cluster's buyer intent decides, not the SERP's format.
    volumes({f"best {primary}": 800, f"{primary} company": 300})
    row = _row(primary, search_intent=intent, ia_page_type="comparison")
    await _top_up([row], context=HEADS_CONTEXT)
    assert row["secondary_keywords_sheet"] == [f"best {primary}", f"{primary} company"]


@pytest.mark.parametrize("page_type", ["blog", "listicle", "article", "guide", "faq", "service"])
@pytest.mark.parametrize("intent", ["commercial", "transactional"])
async def test_a_buyer_intent_row_is_topped_up_whatever_page_type_the_serp_gave_it(volumes, intent, page_type):
    # Phase 5 SERP summaries turn almost any SERP into a "listicle" or "article", and the page type
    # follows; the cluster's own buyer intent decides whether the row targets a buyer.
    volumes({"best web design": 300, "web design company": 120})
    row = _row("web design", search_intent=intent, ia_page_type=page_type)
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["best web design", "web design company"]


@pytest.mark.parametrize("page_type", ["blog", "listicle", "article", "guide", "faq"])
async def test_a_row_with_a_content_page_type_and_no_intent_field_is_topped_up(volumes, page_type):
    # No intent field means nothing says this is not a buyer's page; the page type alone, which
    # a SERP's format can set, is not enough to leave the row empty. Real map rows always carry
    # search_intent, so this only concerns rows built by hand.
    volumes({"best web design": 300})
    row = _row("web design", ia_page_type=page_type)
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["best web design"]


async def test_the_informational_rule_only_skips_those_rows(volumes):
    fake = volumes({"best seo": 900, "seo company": 400})
    rows = [
        _row("seo checklist", search_intent="informational", ia_page_type="guide"),
        _row("seo vs sem", search_intent="commercial", ia_page_type="comparison"),
        _row("what is seo", search_intent="informational", ia_page_type="blog"),
        _row("seo audit", search_intent="informational", ia_page_type="listicle"),
        _row("seo", search_intent="commercial", ia_page_type="service"),
        _row("seo audit", search_intent="transactional", ia_page_type="blog"),
        _row("seo reporting"),  # a row that carries neither field is not skipped
        _row("hubspot alternatives", search_intent="commercial", ia_page_type="service"),
        _row("crm comparison", search_intent="transactional", ia_page_type="comparison"),
        _row("seo packages", search_intent="commercial", ia_page_type="comparison"),
    ]
    await _top_up(rows)
    asked_for = {k for k in fake.asked}
    assert not any("checklist" in k or " vs " in k or "what is" in k for k in asked_for)
    assert not any("alternatives" in k or "comparison" in k for k in asked_for)
    assert any("seo packages" in k for k in asked_for)
    assert any(k.endswith("seo audit") or k.startswith("seo audit") for k in asked_for)
    assert any("seo reporting" in k for k in asked_for)
    assert rows[4]["secondary_keywords_sheet"] == ["best seo", "seo company"]
    assert rows[0]["secondary_keywords_sheet"] == rows[1]["secondary_keywords_sheet"] == rows[2]["secondary_keywords_sheet"] == []


async def test_head_terms_whose_serp_has_a_vs_title_are_topped_up_through_the_real_url_map(volumes):
    """One "X vs Y" title among a SERP's titles makes ``summarize_serp`` return the dominant
    format "comparison", and ``build_final_url_map`` feeds that in as the page type
    ("/compare/seo"). The head terms are still a buyer's searches."""
    from app.services.content_brief import summarize_serp
    from app.services.url_mapping import build_final_url_map

    def keyword(text, role, volume):
        return {"keyword": text, "role": role, "volume": volume, "cpc": 5.0}

    def serp(*titles):
        organic = [
            {"position": i + 1, "title": t, "url": f"https://x{i}.example/p", "domain": f"x{i}.example"}
            for i, t in enumerate(titles)
        ]
        return summarize_serp({"validated": True, "organic": organic}, fallback_intent=None)

    heads = [
        ("seo", "commercial", "SEO vs SEM"),
        ("web design", "commercial", "Web design vs web development"),
        ("emergency plumber", "transactional", "Emergency plumber vs regular plumber"),
        ("crm software", "commercial", "HubSpot vs Salesforce"),
    ]
    clusters = [
        {"name": primary.title(), "primary_keyword": primary, "intent": intent,
         "keywords": [keyword(primary, "Primary", 2000), keyword(f"{primary} cost", "Secondary", 200)]}
        for primary, intent, _ in heads
    ]
    clusters.append(
        {"name": "SEO vs SEM", "primary_keyword": "seo vs sem", "intent": "commercial",
         "keywords": [keyword("seo vs sem", "Primary", 900), keyword("seo vs sem difference", "Secondary", 100)]}
    )
    serp_by_keyword = {
        primary: serp(title, f"{primary.title()} services", "Our work") for primary, _, title in heads
    }
    report = build_final_url_map(
        {"clusters": clusters}, website={"pages": []}, serp_by_keyword=serp_by_keyword, client_domain="acme.example"
    )
    rows = {e["primary_keyword"]: e for e in report["final_url_map"]}
    for primary, intent, _ in heads:
        assert rows[primary]["ia_page_type"] == "comparison", primary  # the scenario
        assert rows[primary]["search_intent"] == intent

    table = {}
    for primary, _, _ in heads:
        table[f"best {primary}"] = 800
        table[f"{primary} company"] = 300
    table["best seo vs sem"] = 700
    fake = volumes(table)
    await _top_up(report["final_url_map"], context=HEADS_CONTEXT)

    for primary, _, _ in heads:
        assert rows[primary]["secondary_keywords_sheet"][-2:] == [f"best {primary}", f"{primary} company"], primary
    # The row that really is a comparison is left alone and never looked up.
    assert not any("vs" in item.split() for item in fake.asked)
    assert "best seo vs sem" not in rows["seo vs sem"]["secondary_keywords_sheet"]


async def test_informational_labelled_head_terms_are_topped_up_through_the_real_url_map(volumes):
    """The intent heuristic labels bare service head terms informational; with a listicle SERP
    their page type is a blog. They are still service head terms, and the informational
    question and checklist rows beside them are not."""
    from app.services.content_brief import summarize_serp
    from app.services.url_mapping import build_final_url_map

    def keyword(text, role, volume):
        return {"keyword": text, "role": role, "volume": volume, "cpc": 5.0}

    def serp(*titles):
        organic = [
            {"position": i + 1, "title": t, "url": f"https://x{i}.example/p", "domain": f"x{i}.example"}
            for i, t in enumerate(titles)
        ]
        return summarize_serp({"validated": True, "organic": organic}, fallback_intent=None)

    heads = ["dental implants", "emergency dentist", "seo"]
    questions = ["what is seo", "how to choose a dentist", "seo checklist"]
    clusters = [
        {"name": text.title(), "primary_keyword": text, "intent": "informational",
         "keywords": [keyword(text, "Primary", 1500), keyword(f"{text} cost", "Secondary", 150)]}
        for text in [*heads, *questions]
    ]
    serp_by_keyword = {text: serp(f"Top 10 {text.title()} Clinics 2026", f"Best {text}") for text in heads}
    report = build_final_url_map(
        {"clusters": clusters}, website={"pages": []}, serp_by_keyword=serp_by_keyword, client_domain="acme.example"
    )
    rows = {e["primary_keyword"]: e for e in report["final_url_map"]}
    for text in heads:
        assert rows[text]["search_intent"] == "informational"  # the scenario
        assert rows[text]["ia_page_type"] in ("blog", "listicle", "article")

    table = {}
    for text in [*heads, *questions]:
        table[f"best {text}"] = 800
        table[f"{text} company"] = 300
    fake = volumes(table)
    await _top_up(report["final_url_map"], context=HEADS_CONTEXT_DENTAL)

    for text in heads:
        assert rows[text]["secondary_keywords_sheet"][-2:] == [f"best {text}", f"{text} company"], text
        for text in questions:
            assert not any(text in asked for asked in fake.asked), text
            # The map folds a question or checklist into its head term, so it is not its own page.
            row = rows.get(text)
            if row is not None:
                sheet = row["secondary_keywords_sheet"]
                assert f"best {text}" not in sheet and f"{text} company" not in sheet, text


async def test_head_terms_whose_serp_looks_like_a_listicle_are_topped_up_through_the_real_url_map(volumes):
    """The shape Phase 5 stores in ``serp_by_keyword``: ``summarize_serp`` turns a SERP of
    titles with a number, "best" or "top" into a listicle, and the map row's page type follows."""
    from app.services.content_brief import summarize_serp
    from app.services.url_mapping import build_final_url_map

    def keyword(text, role, volume):
        return {"keyword": text, "role": role, "volume": volume, "cpc": 5.0}

    def serp(*titles):
        organic = [
            {"position": i + 1, "title": t, "url": f"https://x{i}.example/p", "domain": f"x{i}.example"}
            for i, t in enumerate(titles)
        ]
        return summarize_serp({"validated": True, "organic": organic}, fallback_intent=None)

    clusters = [
        {"name": "Web design", "primary_keyword": "web design", "intent": "commercial",
         "keywords": [keyword("web design", "Primary", 2400), keyword("web design cost", "Secondary", 300)]},
        {"name": "Emergency plumber", "primary_keyword": "emergency plumber", "intent": "transactional",
         "keywords": [keyword("emergency plumber", "Primary", 1900), keyword("24 hour plumber", "Secondary", 200)]},
        {"name": "SEO", "primary_keyword": "seo", "intent": "commercial",
         "keywords": [keyword("seo", "Primary", 5000), keyword("seo audit", "Secondary", 300)]},
        {"name": "SEO checklist", "primary_keyword": "seo checklist", "intent": "informational",
         "keywords": [keyword("seo checklist", "Primary", 800), keyword("seo checklist pdf", "Secondary", 100)]},
    ]
    serp_by_keyword = {
        "web design": serp("Top 10 Web Design Companies 2026", "Web design - Wikipedia", "Best web design agencies"),
        "emergency plumber": serp("24/7 Emergency Plumber near me", "Emergency Plumber - 1300 000 000", "Emergency plumbers 2026"),
    }
    report = build_final_url_map(
        {"clusters": clusters}, website={"pages": []}, serp_by_keyword=serp_by_keyword, client_domain="acme.example"
    )
    rows = {e["primary_keyword"]: e for e in report["final_url_map"]}
    # The scenario: the SERP gave the two head terms a blog-like page type.
    assert rows["web design"]["ia_page_type"] in ("blog", "listicle", "article")
    assert rows["emergency plumber"]["ia_page_type"] in ("blog", "listicle", "article")
    assert rows["web design"]["search_intent"] == "commercial"
    assert rows["emergency plumber"]["search_intent"] == "transactional"

    fake = volumes(
        {
            "best web design": 300,
            "web design company": 120,
            "best seo": 900,
            "emergency plumber company": 90,
            "best emergency plumber": 400,
            "best seo checklist": 700,
        }
    )
    context = build_relevance_context(
        services=["SEO", "Web design", "Emergency plumber"], cdd_keywords=["seo", "web design", "plumber"],
        industry="Digital marketing agency", brand_name="Acme", domain="acme.example",
    )
    await _top_up(report["final_url_map"], context=context)

    assert rows["web design"]["secondary_keywords_sheet"][-2:] == ["best web design", "web design company"]
    assert rows["emergency plumber"]["secondary_keywords_sheet"][-2:] == ["best emergency plumber", "emergency plumber company"]
    assert rows["seo"]["secondary_keywords_sheet"][-1:] == ["best seo"]
    # The informational row is left as it was and was never looked up.
    assert "best seo checklist" not in fake.asked
    assert not any("checklist" in item for item in rows["seo checklist"]["secondary_keywords_sheet"] if item.startswith(("best", "top")))


async def test_the_candidate_rules_reach_the_provider_call(volumes):
    fake = volumes({})
    await _top_up(
        [
            _row("digital marketing agency", search_intent="commercial", ia_page_type="home"),
            _row("dental implants"),
            _row("plumber"),
        ]
    )
    asked = set(fake.asked)
    assert "best digital marketing agency" in asked and "digital marketing agency company" not in asked
    assert "agency dental implants" not in asked and "dental implants agency" in asked
    assert "firm plumber" not in asked and "plumber firm" in asked
    assert "seo best" not in asked and not any(k.startswith(("company ", "agency ", "firm ")) for k in asked)


# ------------------------------------------------------------------- the keyword pool


@pytest.fixture
def pool_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "dataforseo_sandbox", False)
    monkeypatch.setattr(settings, "keyword_pool_enabled", True)


async def _lookup_fetches() -> list[KeywordPoolFetch]:
    async with AsyncSessionLocal() as session:
        return list((await session.execute(select(KeywordPoolFetch))).scalars())


async def test_cached_lookups_are_not_fetched_again(db_session, pool_on, volumes):
    fake = volumes({"best seo": 900, "seo company": 400})
    first = _row("seo")
    await _top_up([first])
    assert len(fake.calls) == 1
    asked_first = list(fake.asked)

    saved = await _lookup_fetches()
    assert {f.kind for f in saved} == {"lookup"}
    # Every candidate is saved, including the ones with no measured volume.
    assert {f.seed_key for f in saved} == set(asked_first)

    second = _row("seo")
    await _top_up([second])
    assert len(fake.calls) == 1  # all 20 came from the pool
    assert second["secondary_keywords_sheet"] == first["secondary_keywords_sheet"] == ["best seo", "seo company"]

    # A new primary is the only thing asked of the provider.
    third = _row("seo")
    other = _row("web design")
    await _top_up([third, other])
    assert len(fake.calls) == 2
    assert fake.calls[1]["keywords"] and all("web design" in k for k in fake.calls[1]["keywords"])


async def test_a_lookup_for_a_wordings_sibling_is_not_taken_for_the_candidate(db_session, pool_on, volumes):
    # The pool also finds a fetch of the same service worded differently ("seo company"
    # for "seo agency"); a lookup is one keyword, so that hit must not stand in for it.
    await save_seed(
        "seo company",
        seed_text="seo company",
        location_code=LOCATION,
        language_code=LANGUAGE,
        min_volume=0,
        limit=1,
        rows=[{"keyword": "seo company", "volume": 400}],
        errors=[],
        kind="lookup",
    )
    sibling = await load_seed(
        "seo agency", location_code=LOCATION, language_code=LANGUAGE, min_volume=0, limit=1, kind="lookup"
    )
    assert sibling is not None  # the premise: the pool does serve the sibling

    fake = volumes({"seo agency": 150})
    row = _row("seo")
    await _top_up([row])
    assert "seo agency" in fake.asked
    assert "seo company" not in fake.asked  # that one really was cached
    assert row["secondary_keywords_sheet"] == ["seo company", "seo agency"]


async def test_lookups_are_stored_only_at_the_country_level_location(db_session, pool_on, volumes):
    fake = volumes({"best seo": 900})
    city = 1000259  # Melbourne; the country is 2036
    await _top_up([_row("seo")], location_code=city, country_location_code=LOCATION)
    assert await _lookup_fetches() == []  # nothing saved
    await _top_up([_row("seo")], location_code=city, country_location_code=LOCATION)
    assert len(fake.calls) == 2  # and nothing read
    assert all(call["location_code"] == city for call in fake.calls)


def _spy_on_the_pool(monkeypatch) -> list[str]:
    """Replace the pool's load and save with spies (a raising stub would be swallowed as a pool failure)."""
    touched: list[str] = []

    async def load(*args, **kwargs):
        touched.append("load")

    async def save(*args, **kwargs):
        touched.append("save")

    monkeypatch.setattr(sk, "load_seed", load)
    monkeypatch.setattr(sk, "save_seeds_bulk", save)
    return touched


@pytest.mark.parametrize(
    "setting",
    [("use_mock_providers", True), ("dataforseo_sandbox", True), ("keyword_pool_enabled", False)],
)
async def test_the_pool_is_bypassed_when_it_is_off_mocked_or_sandboxed(pool_on, volumes, monkeypatch, setting):
    monkeypatch.setattr(get_settings(), *setting)
    fake = volumes({"best seo": 900})
    touched = _spy_on_the_pool(monkeypatch)
    row = _row("seo")
    await _top_up([row])
    assert touched == []  # no load and no save
    assert row["secondary_keywords_sheet"] == ["best seo"]
    assert len(fake.calls) == 1


async def test_the_spy_sees_the_pool_when_it_is_in_use(pool_on, volumes, monkeypatch):
    volumes({"best seo": 900})
    touched = _spy_on_the_pool(monkeypatch)
    await _top_up([_row("seo")])
    assert "load" in touched and "save" in touched


async def test_no_language_means_no_pool(pool_on, volumes, monkeypatch):
    volumes({"best seo": 900})
    touched = _spy_on_the_pool(monkeypatch)
    row = _row("seo")
    await top_up_secondary_keywords(
        [row], location_code=LOCATION, language_code="", country_location_code=LOCATION, relevance_context=CONTEXT
    )
    assert touched == []
    assert row["secondary_keywords_sheet"] == ["best seo"]


async def test_no_location_means_no_pool(pool_on, volumes, monkeypatch):
    volumes({"best seo": 900})
    touched = _spy_on_the_pool(monkeypatch)
    row = _row("seo")
    await top_up_secondary_keywords(
        [row], location_code=0, language_code=LANGUAGE, country_location_code=0, relevance_context=CONTEXT
    )
    assert touched == []
    assert row["secondary_keywords_sheet"] == ["best seo"]


async def test_a_pool_failure_never_fails_the_call(db_session, pool_on, volumes, monkeypatch):
    fake = volumes({"best seo": 900})

    async def broken(*args, **kwargs):
        raise RuntimeError("pool down")

    monkeypatch.setattr(sk, "load_seed", broken)
    monkeypatch.setattr(sk, "save_seeds_bulk", broken)
    row = _row("seo")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["best seo"]
    assert len(fake.calls) == 1


async def test_a_failed_save_does_not_lose_the_measured_variants(db_session, pool_on, volumes, monkeypatch):
    volumes({"best seo": 900})

    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sk, "save_seeds_bulk", broken)
    row = _row("seo")
    await _top_up([row])
    assert row["secondary_keywords_sheet"] == ["best seo"]


async def test_a_failure_building_one_saved_lookup_does_not_lose_the_measured_variants(
    db_session, pool_on, volumes, monkeypatch
):
    real = sk.seed_key
    calls: dict[str, int] = {}

    def flaky(text):
        calls[text] = calls.get(text, 0) + 1
        if text == "top seo" and calls[text] == 2:  # first the read, then the save
            raise RuntimeError("cannot key this one")
        return real(text)

    monkeypatch.setattr(sk, "seed_key", flaky)
    volumes({"best seo": 900, "top seo": 500, "seo company": 400})
    row = _row("seo")

    await _top_up([row])

    assert row["secondary_keywords_sheet"] == ["best seo", "top seo", "seo company"]
    saved = {f.seed_key for f in await _lookup_fetches()}
    assert "top seo" not in saved and {"best seo", "seo company"} <= saved
    assert len(saved) == 9  # the ten candidates but the one that failed


async def test_the_top_up_saves_all_its_lookups_in_one_bulk_call_and_one_commit(db_session, pool_on, volumes, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.services.shared_keyword_pool import store

    volumes({"best seo": 900})
    bulk_calls: list[int] = []
    real_bulk = store.save_seeds_bulk

    async def counting_bulk(items, **kwargs):
        bulk_calls.append(len(items))
        return await real_bulk(items, **kwargs)

    async def no_single_saves(*args, **kwargs):
        raise AssertionError("one save per lookup")

    monkeypatch.setattr(sk, "save_seeds_bulk", counting_bulk)
    monkeypatch.setattr(store, "save_seed", no_single_saves)
    commits: list[int] = []
    listener = lambda _session: commits.append(1)  # noqa: E731
    event.listen(Session, "after_commit", listener)
    try:
        await _top_up([_row("seo"), _row("web design")])
    finally:
        event.remove(Session, "after_commit", listener)

    assert bulk_calls == [20]  # both rows' ten candidates, in one call
    assert len(commits) == 1
    assert len(await _lookup_fetches()) == 20


async def test_a_partial_answer_saves_only_what_it_returned(db_session, pool_on, volumes):
    # A provider error means a missing keyword may be a failure, not "no volume".
    volumes({"best seo": 900}, errors=["dataforseo_volume_failed"])
    await _top_up([_row("seo")])
    assert {f.seed_key for f in await _lookup_fetches()} == {"best seo"}


# -------------------------------------------------- the stored context and the Phase 6 hook


def _stored_demand(**extra) -> dict:
    demand = {
        "services": ["SEO", "Web design"],
        "cdd_keywords": ["seo", "web design"],
        "geographic_focus": "Melbourne, Australia",
        "labs_location_code": LOCATION,
        "competitor_names": ["Rival Digital"],
        "competitor_domains": ["rival.example"],
        "keyword_seeding": {
            "industry_keywords": {"industry": "Digital marketing agency"},
            "seed_synonyms": [{"service": "SEO", "added": ["search engine optimisation"]}],
            "subservice_expansion": [{"service": "SEO", "added": ["seo audit"]}],
            "sub_service_seeding": [{"service": "SEO", "seeds": [{"seed": "local seo"}]}],
        },
    }
    demand.update(extra)
    return demand


def test_the_relevance_context_is_rebuilt_from_what_phase_5_stored():
    ctx = relevance_context_from_stored(
        demand=_stored_demand(),
        commercial={},
        marketing={},
        website={},
        client_name="Acme",
        domain="acme.example",
    )
    assert ctx.services == ["SEO", "Web design"]
    assert ctx.industry == "Digital marketing agency"
    assert ctx.geography == "Melbourne, Australia"
    assert "search engine optimisation" in ctx.service_synonyms
    assert {"seo audit", "local seo"} <= set(ctx.service_subtopics)
    assert ctx.has_business_evidence
    assert ctx.competitor_brand_tokens  # the competitors Phase 5 judged against
    assert ctx.industry_layer is False


async def test_a_client_that_sells_tools_keeps_tool_variants(volumes):
    volumes({"best seo checker": 500})
    software = relevance_context_from_stored(
        demand=_stored_demand(services=["SEO software"], cdd_keywords=["seo software"], keyword_seeding={}),
        commercial={},
        marketing={"industry": "SaaS"},
        website={},
        client_name="Acme",
        domain="acme.example",
    )
    assert software.industry == "SaaS"
    seller = _row("seo checker")
    await _top_up([seller], context=software)
    assert seller["secondary_keywords_sheet"] == ["best seo checker"]

    # The agency's context, rebuilt from its stored industry, does not keep the tool search.
    agency = relevance_context_from_stored(
        demand=_stored_demand(), commercial={}, marketing={}, website={}, client_name="Acme", domain="acme.example"
    )
    buyer = _row("seo checker")
    await _top_up([buyer], context=agency)
    assert buyer["secondary_keywords_sheet"] == []


async def test_the_phase_6_hook_tops_up_rows_and_logs_the_counts(volumes, monkeypatch):
    fake = volumes({"best web design": 300, "seo company": 400})
    report = {
        "final_url_map": [
            _row("seo", ["seo audit"]),
            _row("web design"),
            _row("seo packages", ["a", "b", "c", "d", "e"]),
        ]
    }
    spy = _spy_log(monkeypatch)
    result = await top_up_url_map_secondaries(
        report,
        demand=_stored_demand(),
        commercial={},
        marketing={},
        website={},
        client_name="Acme",
        domain="acme.example",
    )
    rows = report["final_url_map"]
    assert rows[0]["secondary_keywords_sheet"] == ["seo audit", "seo company"]
    assert rows[1]["secondary_keywords_sheet"] == ["best web design"]
    assert rows[2]["secondary_keywords_sheet"] == ["a", "b", "c", "d", "e"]
    assert result["rows_topped_up"] == 2 and result["kept"] == 2
    # The counts are logged, one line; nothing is added to the report that is stored.
    assert set(report) == {"final_url_map"}
    info = [fields for level, event, fields in spy if event == "secondary_keywords_topup"]
    assert info == [
        {"rows_topped_up": 2, "candidates": 20, "candidates_dropped_by_cap": 0, "kept": 2, "empty_rows": 0}
    ]
    # The lookup runs at the same country-level location every other volume in the map was measured at.
    assert fake.calls[0]["location_code"] == LOCATION and fake.calls[0]["language"] == LANGUAGE


async def test_the_hook_never_fails_the_phase(volumes, monkeypatch):
    volumes({"best seo": 900})

    async def broken(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sk, "top_up_secondary_keywords", broken)
    report = {"final_url_map": [_row("seo")]}
    result = await top_up_url_map_secondaries(
        report, demand=_stored_demand(), commercial={}, marketing={}, website={}, client_name="Acme", domain="acme.example"
    )
    assert report["final_url_map"][0]["secondary_keywords_sheet"] == []
    assert result["kept"] == 0 and result["error"] == "RuntimeError"


async def test_the_hook_ignores_a_map_with_no_rows(volumes):
    fake = volumes({"best seo": 900})
    for report in ({"final_url_map": []}, {}):
        result = await top_up_url_map_secondaries(
            report, demand=_stored_demand(), commercial={}, marketing={}, website={}, client_name="Acme", domain="acme.example"
        )
        assert result == {
            "rows_topped_up": 0,
            "candidates": 0,
            "candidates_dropped_by_cap": 0,
            "kept": 0,
            "empty_rows": 0,
            "lookup_errors": [],
        }
    for report in (None, "garbage", ["x"]):
        assert (
            await top_up_url_map_secondaries(
                report, demand=_stored_demand(), commercial={}, marketing={}, website={}, client_name="Acme", domain="acme.example"
            )
            is None
        )
    assert fake.calls == []


# ------------------------------------------------------------ through the Phase 6 entry point


async def _fake_click_depth(seed_url, *, page_cap=80):
    return {
        "urls_crawled": 1,
        "issues": [],
        "max_depth": 0,
        "pages_by_depth": {},
        "orphan_pages": [],
        "phantom_directories": [],
    }


async def test_site_architecture_tops_up_the_final_url_map_rows(volumes, monkeypatch):
    spy = _spy_log(monkeypatch)
    fake = volumes({"best web design": 300, "web design company": 120, "best seo services": 400})
    monkeypatch.setattr(site_architecture, "measure_click_depth", _fake_click_depth)
    demand = _stored_demand(
        cluster_report={
            "clusters": [
                {
                    "name": "SEO services",
                    "primary_keyword": "seo services",
                    "intent": "commercial",
                    "keywords": [
                        {"keyword": "seo services", "role": "Primary", "volume": 2400, "cpc": 9.0},
                        {"keyword": "seo audit", "role": "Secondary", "volume": 300, "cpc": 4.0},
                    ],
                },
                {
                    "name": "Web design",
                    "primary_keyword": "web design",
                    "intent": "commercial",
                    "keywords": [{"keyword": "web design", "role": "Primary", "volume": 1900, "cpc": 6.0}],
                },
            ]
        }
    )

    blueprint = await site_architecture.run_site_architecture_plan(
        client_name="Acme",
        primary_url="https://acme.example",
        domain="acme.example",
        demand=demand,
        commercial={},
        website={"pages": []},
    )

    rows = {e["primary_keyword"]: e for e in blueprint["url_map_report"]["final_url_map"]}
    assert rows["web design"]["secondary_keywords_sheet"] == ["best web design", "web design company"]
    assert rows["web design"]["secondary_keywords"] == ["best web design", "web design company"]
    # "seo services" already names a provider, so it takes no "... company"; "best ..." fits.
    assert rows["seo services"]["secondary_keywords_sheet"] == ["seo audit", "best seo services"]
    assert len(fake.calls) == 1
    # Nothing is added to the stored report; the counts are logged instead.
    assert "secondary_keyword_topup" not in blueprint["url_map_report"]
    assert not any("secondary" in key and "topup" in key for key in blueprint)
    info = [fields for level, event, fields in spy if event == "secondary_keywords_topup"]
    assert len(info) == 1
    assert info[0]["rows_topped_up"] == 2 and info[0]["kept"] == 3 and info[0]["empty_rows"] == 0
    # The origin of a kept variant is recorded nowhere on the row.
    assert not any("modifier" in key or "topup" in key for key in rows["web design"])
