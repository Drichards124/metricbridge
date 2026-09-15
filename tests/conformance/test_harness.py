"""The harness itself: a conformance suite that can pass by accident has proved nothing."""

from datetime import date, datetime
from decimal import Decimal

import harness
import pytest
from harness import CaseError, compare, load_case, normalise, run_case
from hypothesis import given
from hypothesis import strategies as st
from storefront_data import storefront_database

VALID = """\
id: revenue_by_channel
catalog: storefront
request:
  metric: revenue
  date_range: {start_date: "2026-07-01", end_date: "2026-09-30"}
  dimensions: [channel]
reference_sql: |
  SELECT channel, SUM(amount) AS revenue FROM storefront.fct_order_lines
  GROUP BY channel ORDER BY channel NULLS LAST
"""


@pytest.fixture(autouse=True)
def hand_worked_storefront(monkeypatch):
    """The harness's own tests run on the seed whose answers are worked out by hand."""
    catalog = (harness.FIXTURES / "storefront", storefront_database)
    monkeypatch.setitem(harness.CATALOGS, "storefront", catalog)


class TestLoadCase:
    def test_an_unknown_field_is_refused(self, tmp_path):
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(VALID + "expected_rows: []\n")

        with pytest.raises(CaseError, match="expected_rows"):
            load_case(path)

    @pytest.mark.parametrize(
        "text",
        [
            VALID.split("reference_sql:")[0],
            VALID + "refusal: unknown_dimension\n",
            VALID + "manifest_refusal_message: fact_to_fact_join\n",
        ],
        ids=["neither", "reference_and_refusal", "reference_and_manifest_refusal"],
    )
    def test_a_case_states_exactly_one_outcome(self, tmp_path, text):
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(text)

        with pytest.raises(CaseError, match="exactly one of"):
            load_case(path)

    @pytest.mark.parametrize("suffix", [".yml", ".yaml"])
    def test_the_id_is_the_file_name(self, tmp_path, suffix):
        matching = tmp_path / f"revenue_by_channel{suffix}"
        matching.write_text(VALID)
        renamed = tmp_path / f"revenue_by_region{suffix}"
        renamed.write_text(VALID)

        assert load_case(matching).id == "revenue_by_channel"
        with pytest.raises(CaseError, match="revenue_by_region"):
            load_case(renamed)

    def test_a_typo_in_the_request_is_refused_when_the_case_loads(self, tmp_path):
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(VALID.replace("dimensions:", "dimension:"))

        with pytest.raises(CaseError, match="dimension"):
            load_case(path)

    def test_a_grouped_reference_states_its_order(self, tmp_path):
        # MetricBridge's rows are totally ordered, and the comparison keeps that order.
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(VALID.replace(" ORDER BY channel NULLS LAST", ""))

        with pytest.raises(CaseError, match="ORDER BY"):
            load_case(path)

    def test_a_reference_that_does_not_parse_is_refused(self, tmp_path):
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(VALID.replace("GROUP BY channel", "GROUP BY BY channel"))

        with pytest.raises(CaseError, match="reference_sql"):
            load_case(path)

    def test_a_known_divergence_names_an_entry_in_the_failure_modes_catalog(self, tmp_path):
        path = tmp_path / "revenue_by_channel.yml"
        path.write_text(VALID + "known_divergence: Anchor dropping in inactive periods\n")
        assert load_case(path).known_divergence == "Anchor dropping in inactive periods"

        path.write_text(VALID + "known_divergence: a gap nobody wrote down\n")
        with pytest.raises(CaseError, match="failure-modes"):
            load_case(path)

    def test_a_single_row_reference_needs_no_order(self, tmp_path):
        path = tmp_path / "revenue.yml"
        path.write_text(
            "id: revenue\n"
            "catalog: storefront\n"
            "request:\n"
            "  metric: revenue\n"
            '  date_range: {start_date: "2026-07-01", end_date: "2026-09-30"}\n'
            "reference_sql: SELECT SUM(amount) AS revenue FROM storefront.fct_order_lines\n"
        )

        assert load_case(path).reference_sql.startswith("SELECT")


class TestNormalise:
    def test_a_decimal_is_the_same_number_whatever_its_scale(self):
        assert normalise(Decimal("150.00")) == normalise(Decimal("150.0")) == "150"
        assert normalise(Decimal(100)) == "100"  # not 1E+2
        assert normalise(Decimal("0.50")) == "0.5"
        assert normalise(Decimal("-0.00")) == normalise(Decimal(0)) == "0"

    def test_dates_and_timestamps_are_iso(self):
        assert normalise(date(2026, 7, 1)) == "2026-07-01"
        # Naive, as the engines return a TIMESTAMP.
        assert normalise(datetime.fromisoformat("2026-07-01T13:05")) == "2026-07-01T13:05:00"

    def test_nulls_integers_and_text_pass_through_distinct(self):
        # A missing value is not an empty string and not zero.
        values = [None, "", 0, "0", "web"]
        assert [normalise(v) for v in values] == values
        assert len({repr(normalise(v)) for v in values}) == len(values)

    def test_a_float_is_compared_at_fixed_precision(self):
        assert normalise(0.1 + 0.2) == normalise(0.3) == "0.300000000"
        assert normalise(-0.0) == normalise(0.0) == "0.000000000"

    @given(st.decimals(allow_nan=False, allow_infinity=False), st.integers(1, 6))
    def test_an_equal_decimal_at_another_scale_normalises_alike(self, a, zeros):
        rescaled = a + Decimal("0." + "0" * zeros)  # same value, more places, and -0 becomes 0
        assert normalise(rescaled) == normalise(a)

    @given(
        st.decimals(allow_nan=False, allow_infinity=False),
        st.decimals(allow_nan=False, allow_infinity=False),
    )
    def test_two_decimals_normalise_alike_only_when_they_are_equal(self, a, b):
        # A lossy normaliser would make a wrong answer compare equal to the right one.
        assert (normalise(a) == normalise(b)) == (a == b)


def storefront_case(tmp_path, text=VALID):
    path = tmp_path / "revenue_by_channel.yml"
    path.write_text(text)
    return load_case(path)


# MetricBridge's answer as the agent receives it: decimals as strings, NULL as None.
ANSWER = {
    "ok": True,
    "columns": ["channel", "revenue"],
    "rows": [
        {"channel": "store", "revenue": "80.00"},
        {"channel": "web", "revenue": "150.00"},
        {"channel": None, "revenue": "30.00"},
    ],
}
REFERENCE = (
    ["channel", "revenue"],
    [("store", Decimal("80.0")), ("web", Decimal(150)), (None, Decimal("30.00"))],
)


class TestCompare:
    def test_the_same_answer_has_no_differences(self, tmp_path):
        assert compare(ANSWER, storefront_case(tmp_path), REFERENCE) == []

    def test_a_changed_value_is_a_difference(self, tmp_path):
        columns, rows = REFERENCE
        wrong = (columns, [rows[0], ("web", Decimal("150.01")), rows[2]])

        (difference,) = compare(ANSWER, storefront_case(tmp_path), wrong)
        assert "row 1" in difference and "revenue" in difference

    def test_rows_in_another_order_are_a_difference(self, tmp_path):
        # The order is part of the answer: an agent reads "top N" off it.
        columns, rows = REFERENCE
        swapped = (columns, [rows[1], rows[0], rows[2]])

        differences = compare(ANSWER, storefront_case(tmp_path), swapped)
        assert {d.split(",")[0] for d in differences} == {"row 0", "row 1"}

    @pytest.mark.parametrize("side", ["answer", "reference"])
    def test_a_skipped_row_is_a_difference(self, tmp_path, side):
        answer, (columns, rows) = ANSWER, REFERENCE
        if side == "answer":
            answer = {**ANSWER, "rows": ANSWER["rows"][:-1]}
        else:
            rows = rows[:-1]

        differences = compare(answer, storefront_case(tmp_path), (columns, rows))
        assert any("3 rows" in d and "2 rows" in d for d in differences)

    def test_a_period_matches_the_midnight_timestamp_date_trunc_returns(self, tmp_path):
        # DuckDB 1.5.5: date_trunc('month', DATE '2026-07-20') is TIMESTAMP 2026-07-01 00:00:00.
        answer = {"ok": True, "columns": ["period"], "rows": [{"period": "2026-07-01"}]}
        midnight = datetime.fromisoformat("2026-07-01T00:00")
        one_past = datetime.fromisoformat("2026-07-01T00:01")

        assert compare(answer, storefront_case(tmp_path), (["period"], [(midnight,)])) == []
        assert compare(answer, storefront_case(tmp_path), (["period"], [(one_past,)])) != []

    @pytest.mark.parametrize("side", ["answer", "reference"])
    def test_a_null_on_one_side_only_is_a_difference(self, tmp_path, side):
        answer, (columns, rows) = ANSWER, REFERENCE
        if side == "answer":
            nulled = {"channel": "store", "revenue": None}
            answer = {**ANSWER, "rows": [nulled, *ANSWER["rows"][1:]]}
        else:
            rows = [("store", None), *rows[1:]]

        (difference,) = compare(answer, storefront_case(tmp_path), (columns, rows))
        assert difference.startswith("row 0, revenue")

    def test_a_value_that_is_not_the_reference_type_is_a_difference(self, tmp_path):
        # A text value where the reference has a number, not a crash in the harness.
        answer = {**ANSWER, "rows": [{"channel": "store", "revenue": "n/a"}, *ANSWER["rows"][1:]]}

        (difference,) = compare(answer, storefront_case(tmp_path), REFERENCE)
        assert difference.startswith("row 0, revenue") and "'n/a'" in difference

    @pytest.mark.parametrize(
        "columns", [["revenue", "channel"], ["channel", "total"]], ids=["reordered", "renamed"]
    )
    def test_different_columns_are_a_difference(self, tmp_path, columns):
        _, rows = REFERENCE

        differences = compare(ANSWER, storefront_case(tmp_path), (columns, rows))
        assert differences and differences[0].startswith("columns:")


REFUSAL_CASE = VALID.split("reference_sql:")[0] + "refusal: unknown_dimension\n"


def refused(*codes):
    return {"ok": False, "errors": [{"code": code, "message": "refused"} for code in codes]}


class TestCompareRefusals:
    def test_the_expected_refusal_has_no_differences(self, tmp_path):
        case = storefront_case(tmp_path, REFUSAL_CASE)

        assert compare(refused("unknown_dimension"), case, None) == []

    def test_a_different_refusal_is_a_difference(self, tmp_path):
        case = storefront_case(tmp_path, REFUSAL_CASE)

        (difference,) = compare(refused("unknown_metric"), case, None)
        assert "unknown_dimension" in difference and "unknown_metric" in difference

    def test_an_answer_where_a_refusal_was_expected_is_a_difference(self, tmp_path):
        case = storefront_case(tmp_path, REFUSAL_CASE)

        (difference,) = compare(ANSWER, case, None)
        assert "unknown_dimension" in difference

    def test_a_refusal_where_rows_were_expected_is_a_difference(self, tmp_path):
        (difference,) = compare(refused("unknown_metric"), storefront_case(tmp_path), REFERENCE)
        assert "unknown_metric" in difference


class TestRunCase:
    def test_metricbridge_and_the_reference_agree_on_the_seeded_storefront(self, tmp_path):
        case = storefront_case(tmp_path)

        answer, reference = run_case(case)

        # Worked by hand from tests/storefront_data.py, so neither side can drift unnoticed.
        assert reference == (
            ["channel", "revenue"],
            [("store", Decimal("80.00")), ("web", Decimal("150.00")), (None, Decimal("30.00"))],
        )
        assert compare(answer, case, reference) == []

    def test_a_wrong_reference_turns_the_case_red(self, tmp_path):
        case = storefront_case(tmp_path, VALID.replace("SUM(amount)", "SUM(amount) * 2"))

        answer, reference = run_case(case)

        assert len(compare(answer, case, reference)) == 3


ORDERS = """\
semantic_models:
  - name: orders
    table: storefront.fct_order_lines
    description: One row per order line.
    entities:
      - {name: customer, type: foreign, expr: customer_id}
    dimensions:
      - {name: order_date, type: time, time_granularity: day, is_partition: true}
    measures:
      - {name: revenue, agg: sum, expr: amount}
metrics:
  - name: revenue
    type: simple
    measure: revenue
    description: Recognised order revenue.
    tier: certified
    owner: finance
    domain: sales
"""
TICKETS = """\
semantic_models:
  - name: tickets
    table: support.fct_tickets
    description: One row per support ticket.
    entities:
      - {name: customer, type: foreign, expr: customer_id}
    dimensions:
      - {name: opened_date, type: time, time_granularity: day, is_partition: true}
    measures:
      - {name: ticket_count, agg: count, expr: ticket_id}
"""


def manifest_case(tmp_path, catalog, message):
    return storefront_case(
        tmp_path,
        VALID.replace("catalog: storefront", f"catalog: {catalog}").split("reference_sql:")[0]
        + f"manifest_refusal_message: {message}\n",
    )


@pytest.fixture
def fanned_catalog(tmp_path, monkeypatch):
    """Two facts sharing `customer` with no model that owns it: refused as many-to-many at load.

    With a primary `customers` model the same two facts load, each joined N:1 to it.
    """
    directory = tmp_path / "fanned"
    directory.mkdir()
    (directory / "orders.yml").write_text(ORDERS)
    (directory / "tickets.yml").write_text(TICKETS)
    _, seed = harness.CATALOGS["storefront"]
    monkeypatch.setitem(harness.CATALOGS, "fanned", (directory, seed))
    return "fanned"


class TestManifestRefusals:
    def test_the_expected_manifest_refusal_has_no_differences(self, tmp_path, fanned_catalog):
        case = manifest_case(tmp_path, fanned_catalog, "many-to-many")

        answer, reference = run_case(case)

        assert compare(answer, case, reference) == []

    def test_a_different_manifest_refusal_is_a_difference(self, tmp_path, fanned_catalog):
        case = manifest_case(tmp_path, fanned_catalog, "no partition dimension")

        answer, reference = run_case(case)

        (difference,) = compare(answer, case, reference)
        assert "no partition dimension" in difference and "many-to-many" in difference

    def test_a_manifest_that_loads_where_a_refusal_was_expected_is_a_difference(self, tmp_path):
        case = manifest_case(tmp_path, "storefront", "many-to-many")

        answer, reference = run_case(case)

        (difference,) = compare(answer, case, reference)
        assert "many-to-many" in difference and "loaded" in difference
