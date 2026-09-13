"""The census reads dbt-shaped manifests and reports what they declare.

Extraction is tested against local fixtures so the gates never need the network; only the
regeneration run fetches the real corpus.
"""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from manifest_census import STATUS, collect_features, report

DBT_SHAPED = textwrap.dedent("""\
    semantic_model:
      name: bookings_source
      node_relation: {schema_name: $source_schema, alias: fct_bookings}
      defaults:
        agg_time_dimension: ds
      entities:
        - {name: booking, type: primary, expr: booking_id}
        - {name: listing, type: foreign, expr: listing_id}
      dimensions:
        - name: ds
          type: time
          type_params: {time_granularity: day}
          is_partition: true
        - name: booking_hour
          type: time
          type_params: {time_granularity: hour}
        - name: is_instant
          type: categorical
      measures:
        - {name: bookings, agg: sum, expr: 1}
        - {name: median_price, agg: median, expr: price}
        - name: instant_bookings
          agg: sum
          agg_time_dimension: ds
          non_additive_dimension: {name: ds, window_choice: max}
    """)

SCD_SHAPED = textwrap.dedent("""\
    semantic_model:
      name: scd_listings
      node_relation: {schema_name: $source_schema, alias: dim_listings}
      entities:
        - {name: listing, type: natural, expr: listing_id}
      dimensions:
        - name: window_start
          type: time
          type_params:
            time_granularity: day
            validity_params: {is_start: true}
    """)

METRICS = textwrap.dedent("""\
    metric:
      name: bookings
      type: simple
      type_params: {measure: bookings}
    ---
    metric:
      name: bookings_growth
      type: derived
      type_params:
        expr: bookings - bookings_prior
        metrics:
          - {name: bookings, alias: bookings_prior, offset_window: 1 month}
    ---
    metric:
      name: instant_share
      type: ratio
      filter: "{{ Dimension('listing__is_lux') }}"
      type_params: {numerator: instant_bookings, denominator: bookings}
    """)


MULTI_HOP = textwrap.dedent("""\
    semantic_model:
      name: bookings
      node_relation: {schema_name: $source_schema, alias: fct_bookings}
      entities:
        - {name: booking, type: primary, expr: booking_id}
        - {name: listing, type: foreign, expr: listing_id}
      dimensions:
        - {name: ds, type: time, type_params: {time_granularity: day}, is_partition: true}
      measures:
        - {name: bookings, agg: sum, expr: 1}
    ---
    semantic_model:
      name: listings
      node_relation: {schema_name: $source_schema, alias: dim_listings}
      entities:
        - {name: listing, type: primary, expr: listing_id}
        - {name: host, type: foreign, expr: host_id}
      dimensions:
        - {name: country, type: categorical}
    ---
    semantic_model:
      name: hosts
      node_relation: {schema_name: $source_schema, alias: dim_hosts}
      entities:
        - {name: host, type: primary, expr: host_id}
      dimensions:
        - {name: host_tier, type: categorical}
    """)


@pytest.fixture(scope="module")
def census(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    (root / "simple_manifest" / "semantic_models").mkdir(parents=True)
    (root / "simple_manifest" / "semantic_models" / "bookings.yaml").write_text(DBT_SHAPED)
    (root / "simple_manifest" / "metrics.yaml").write_text(METRICS)
    (root / "scd_manifest").mkdir()
    (root / "scd_manifest" / "listings.yaml").write_text(SCD_SHAPED)
    (root / "multi_hop_join_manifest").mkdir()
    # A real two-hop chain: bookings --listing--> listings --host--> hosts.
    (root / "multi_hop_join_manifest" / "models.yaml").write_text(MULTI_HOP)
    return collect_features(root)


def test_counts_files_models_and_metrics(census):
    assert (census.files, census.semantic_models, census.metrics) == (4, 5, 3)


@pytest.mark.parametrize(
    "feature",
    [
        "metric:simple",
        "metric:derived",
        "metric:ratio",
        "metric:filter",
        "metric:offset_window",
        "agg:sum",
        "agg:median",
        "entity:natural",
        "dimension:validity_params",
        "dimension:sub_day_granularity",
        "dimension:is_partition",
        "measure:non_additive_dimension",
        "measure:agg_time_dimension",
        "model:defaults_agg_time_dimension",
        "join:multi_hop",
    ],
)
def test_finds_the_features_that_decide_our_coverage(census, feature):
    assert census.features[feature] >= 1, f"{feature} not detected"


def test_features_are_attributed_to_their_manifest(census):
    assert census.projects["scd_manifest"]["entity:natural"] == 1
    assert "entity:natural" not in census.projects["simple_manifest"]


def test_every_counted_feature_has_a_declared_status(census):
    assert [f for f in census.features if f not in STATUS] == []


def test_report_lists_gaps_and_counts(census):
    text = report(census)
    assert "| `metric:derived` |" in text
    assert "## Gaps to answer" in text
    assert "measure:agg_time_dimension" in text.split("## Gaps to answer")[1]
