# Manifest coverage

A feature census of public semantic manifests, asking whether the MetricBridge model can
express what real semantic layers declare. Source: dbt-labs/metricflow fixture manifests
(Apache-2.0) at `e2f17cbb6563`, fetched and never vendored. Regenerate with
`make census`.

**84 files · 57 semantic models · 154 metrics · 17 manifests**

| Feature | Uses | Status | Note |
| --- | --- | --- | --- |
| `dimension:time` | 63 | supported |  |
| `metric:derived` | 63 | deferred | D9 — Phase 2 |
| `metric:simple` | 59 | supported |  |
| `agg:sum` | 42 | supported |  |
| `dimension:categorical` | 41 | supported |  |
| `entity:primary` | 39 | supported |  |
| `model:defaults_agg_time_dimension` | 37 | gap | same as above — 1.4 (D11) |
| `entity:foreign` | 35 | supported |  |
| `metric:offset_window` | 19 | gap | period-over-period offsets — Phase 2 |
| `model:primary_entity` | 17 | supported |  |
| `metric:filter` | 16 | gap | metric-level filters are not modelled yet |
| `metric:cumulative` | 13 | supported |  |
| `metric:ratio` | 11 | supported |  |
| `dimension:is_partition` | 10 | supported |  |
| `metric:offset_to_grain` | 9 | gap | period-over-period offsets — Phase 2 |
| `entity:unique` | 8 | supported |  |
| `metric:conversion` | 8 | deferred | D9 — Phase 2 |
| `agg:count_distinct` | 7 | supported | roll-up recomputed from base rows in 1.4 (D11) |
| `dimension:validity_params` | 6 | deferred | SCD validity windows — Phase 2 (D11) |
| `measure:agg_time_dimension` | 5 | gap | aggregation time vs partition column — 1.4 (D11) |
| `agg:count` | 4 | supported |  |
| `agg:percentile` | 4 | deferred | D9 — Phase 2 |
| `dimension:sub_day_granularity` | 4 | deferred | D9 — Phase 2 |
| `entity:natural` | 3 | deferred | SCD type 2 — Phase 2 (D11) |
| `join:multi_hop` | 3 | deferred | single-hop only in v0 (declined #5) |
| `measure:non_additive_dimension` | 3 | supported | declared roll-up (D8) |
| `agg:average` | 2 | supported | roll-up recomputed from base rows in 1.4 (D11) |
| `agg:max` | 2 | supported |  |
| `agg:min` | 2 | supported |  |
| `agg:sum_boolean` | 2 | deferred | D9 — Phase 2 |
| `agg:median` | 1 | deferred | D9 — Phase 2 |

## Gaps to answer

- `measure:agg_time_dimension` — aggregation time vs partition column — 1.4 (D11)
- `metric:filter` — metric-level filters are not modelled yet
- `metric:offset_to_grain` — period-over-period offsets — Phase 2
- `metric:offset_window` — period-over-period offsets — Phase 2
- `model:defaults_agg_time_dimension` — same as above — 1.4 (D11)
