"""Discovery is lexical and deterministic: the same phrasing always returns the same candidates.

The golden cases below are the protocol's first step. When one of them is wrong, the fix belongs in
the manifest — a missing synonym or a thin description — far more often than in the ranking.
"""

from pathlib import Path
from typing import ClassVar

import pytest

from metricbridge.discovery import MetricIndex, rank_names
from metricbridge.manifest import load_manifest

STOREFRONT = Path(__file__).parent / "fixtures" / "storefront"


@pytest.fixture(scope="module")
def index():
    return MetricIndex(load_manifest(STOREFRONT))


GOLDEN = [
    ("revenue", "revenue"),
    ("revenue by region", "revenue"),
    ("revenue last quarter", "revenue"),
    ("sales", "revenue"),
    ("sales last quarter", "revenue"),
    ("gmv", "revenue"),
    ("net revenue after refunds", "revenue"),
    ("revenue by customer segment", "revenue"),
    ("order count", "order_count"),
    ("how many orders", "order_count"),
    ("distinct orders", "order_count"),
    ("orders by channel", "order_count"),
    ("number of orders placed", "order_count"),
    ("average order value", "average_order_value"),
    ("aov", "average_order_value"),
    ("revenue per order", "average_order_value"),
    ("inventory", "inventory_on_hand"),
    ("inventory on hand", "inventory_on_hand"),
    ("stock at the end of the period", "inventory_on_hand"),
    ("stock at the start of the period", "opening_stock"),
    ("stock at the end of the month", "inventory_on_hand"),
    ("units on hand by warehouse", "inventory_on_hand"),
    ("arr", "trailing_12m_revenue"),
    ("ttm revenue", "trailing_12m_revenue"),
    ("trailing twelve months revenue", "trailing_12m_revenue"),
    ("subscription revenue over the last year", "trailing_12m_revenue"),
    ("web revenue", "web_revenue"),
    ("revenue from the web channel", "web_revenue"),
]


@pytest.mark.parametrize(("phrasing", "expected"), GOLDEN)
def test_golden_phrasings_find_the_right_metric(index, phrasing, expected):
    found = [metric.name for metric, _ in index.search(phrasing, certified_only=False, limit=3)]
    assert found, f"{phrasing!r} found nothing"
    assert expected in found[:2], f"{phrasing!r} -> {found}"


def test_a_phrasing_matching_several_metrics_equally_returns_them_all(index):
    """ "Units in stock" is opening, closing, or the undeclared raw measure. Ranking cannot know
    which, and inventing a winner is how an agent reports the wrong number confidently."""
    found = [m.name for m, _ in index.search("units in stock", certified_only=False, limit=5)]
    assert {"inventory_on_hand", "opening_stock", "stock_level"} <= set(found)


def test_a_phrasing_sharing_no_word_with_the_catalog_finds_nothing(index):
    """The honest limit of lexical search: it ranks vocabulary the manifest actually contains.

    "how much did we sell" reaches nothing, because no metric says "sell". The fix is a synonym in
    the manifest; the trigger for reaching further (embeddings) is measured recall failure, which
    is what this test makes visible rather than hiding.
    """
    assert index.search("how much did we sell", certified_only=False) == []


def test_search_is_deterministic(index):
    first = index.search("revenue by region", limit=5)
    second = index.search("revenue by region", limit=5)
    assert [(m.name, s) for m, s in first] == [(m.name, s) for m, s in second]


def test_certified_only_is_the_default(index):
    certified = {m.name for m, _ in index.search("revenue", limit=10)}
    everything = {m.name for m, _ in index.search("revenue", certified_only=False, limit=10)}
    assert "gross_revenue" not in certified  # deprecated
    assert "revenue_month_to_date" not in certified  # experimental
    assert {"gross_revenue", "revenue_month_to_date"} <= everything


def test_domain_narrows_the_catalog(index):
    assert index.domains() == ["operations", "sales"]
    operations = {m.name for m, _ in index.search("revenue", domain="operations", limit=10)}
    assert operations == set()
    sales = {m.name for m, _ in index.search("revenue", domain="sales", limit=10)}
    assert "revenue" in sales


def test_a_metric_is_findable_by_the_cuts_it_authorises(index):
    found = [m.name for m, _ in index.search("warehouse", certified_only=False, limit=3)]
    assert "inventory_on_hand" in found


def test_naming_a_metric_outright_beats_merely_sharing_words(index):
    """`revenue by region` is a question about revenue, not about every metric mentioning both."""
    top, _ = index.search("revenue by region", limit=1)[0]
    assert top.name == "revenue"


class TestSuggestionRanking:
    names: ClassVar[list[str]] = ["channel", "customer__region", "product__category", "order_date"]

    def test_a_misspelling_is_matched_by_shape(self):
        assert rank_names("chanel", self.names)[0] == "channel"

    def test_a_wrong_concept_is_matched_by_words(self):
        ranked = rank_names(
            "colour",
            self.names,
            descriptions={"product__category": "Product category, such as colour and size"},
        )
        assert ranked[0] == "product__category"

    def test_anything_else_falls_back_to_what_exists(self):
        assert set(rank_names("zzzz", self.names)) == set(self.names)

    def test_the_list_is_capped(self):
        many = [f"cut_{i:03d}" for i in range(200)]
        assert len(rank_names("nothing_like_it", many)) == 25
