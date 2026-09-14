# Failure modes

Every way MetricBridge could return a **plausible wrong number**, and what stops it. A wrong number
that raises no error is the failure this project exists to prevent, so each class below is either
guarded by a named test, refused outright, or recorded here as unguarded. "Unguarded" is an honest
status, not a gap to hide: the phase-close audit reads this table (exit criterion E12).

Status key: **guarded** — a test fails if the behaviour regresses · **refused** — the manifest or
request is rejected, so the case cannot arise · **unguarded** — known, not yet addressed.

## Semantics of a measure

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Snapshot summed across time | Twelve monthly snapshots of stock count the same pallet twelve times | guarded | `tests/test_validate.py::TestRefusals::test_snapshot_without_a_declared_rollup_is_refused` |
| Snapshot rolled up the wrong way | Month-end stock computed as an average, or from the first day | guarded | the compiler ranks by the declared window and keeps one row per group (`tests/test_compiler.py::TestSnapshotMetrics`) |
| Grain finer than the table | A weekly table asked for a daily figure invents rows | guarded | `tests/test_validate.py::test_a_grain_finer_than_the_table_is_refused` |
| `count_distinct` rolled up | Distinct orders per day summed into a month double-counts repeat orders | guarded | `tests/test_compiler.py::TestAggregations::test_a_distinct_count_is_recomputed_at_the_requested_grain` |
| `average` rolled up | An average of daily averages is not the period average | guarded | same: every aggregate is computed from base rows, never from a finer aggregate |
| `percentile` / `median` | Not additive, and engines disagree on method | refused | not supported in this version (D9) |
| Ratio computed as an average of ratios | The average of daily ratios is not the period ratio | guarded | `tests/test_compiler.py::TestRatioMetrics::test_a_ratio_divides_two_aggregates_after_grouping` |
| Zero denominator reported as zero | "No orders" reads as "£0 average order value" | guarded | `tests/test_compiler.py::TestRatioMetrics::test_a_zero_denominator_yields_null_not_zero` |

## Joins

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Many-to-many join | Rows fan out and every measure inflates | refused | `tests/test_manifest_invalid.py` (`many-to-many-through-shared-foreign-entity`) |
| Inner join to a dimension | Rows with a null key vanish, shrinking the denominator | guarded | joins are always LEFT, and the answer reports `null_key_rows` per joined entity (`tests/test_engine.py::TestAnswers::test_each_join_reports_its_null_key_rows_beside_the_rows`) |
| Null join keys in ratio and cumulative answers | Rows with an empty key land in a `NULL` cut, and nothing says how many | unguarded | these shapes do not count null keys yet and answer `null_key_rows: null`, never `{}` (`tests/test_engine.py::TestAnswers::test_shapes_that_do_not_count_null_keys_say_so`); counted in 1.6c |
| Join key that matches no dimension row | An order line with `customer_id = 99` and no such customer lands in the blank cut like an empty key, but `null_key_rows` counts only empty keys: one unmatched line reported where there are two | unguarded | known Phase 1 limitation, disclosed in the `query_metric` description; pre-limit unreconciled accounting in 1.6c |
| Null-key counts lost to the row limit | When the limit cuts the answer, the count is `null` although an exact one was computable | unguarded | `null` rather than an under-count (`tests/test_engine.py::TestAnswers::test_nothing_is_claimed_about_rows_the_limit_cut_off`); exact counts in 1.6c |
| Fan trap (two facts, one shared dimension) | One fact's measure multiplies by the other's row count | unguarded | 1.7 corpus (D11) |
| Chasm trap (two unrelated facts joined through a dimension) | A cartesian product presented as a total | unguarded | 1.7 corpus (D11) |
| Declared key is not actually unique | A "N:1" join fans out because the data disagrees with the manifest | refused | every key a join targets is checked when the server starts, and a repeated non-null value stops it (`tests/test_verify.py::test_a_duplicated_join_key_is_refused_without_quoting_its_values`; over generated keys, `tests/test_verify.py::test_a_key_is_refused_exactly_when_a_value_repeats`) |
| Key duplicated after the server started | The startup check passed, then a load repeats a customer: the join fans out until the next restart | unguarded | checked at startup only; restart the server after changing a dimension table |
| SCD type 2 dimension | Joining without validity windows attributes history to the current row | refused | `natural` entities not supported (D9); Phase 2 |

## Time

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Unbounded scan | No date bound: cost and blast radius unbounded | guarded | `tests/test_validate.py::TestRefusals::test_missing_date_range` |
| Inclusive end date on a timestamp column | The final day is silently dropped | guarded | `tests/test_compiler.py::TestDateBounds::test_the_range_is_half_open_so_the_last_day_is_not_dropped` |
| ClickHouse placeholders lack a type | sqlglot emits `{name: }`, which ClickHouse rejects | unguarded | the ClickHouse adapter supplies the type in 1.7; no ClickHouse execution exists yet |
| Trailing window not widened | A trailing-12-month metric pruned to the output window returns a wrong number | guarded | `tests/test_compiler.py::TestCumulativeAgainstDuckDB::test_a_trailing_total_accumulates_across_periods` — executed, not just inspected |
| **Anchor dropping in inactive periods** | A period with no base rows never becomes an anchor, so its *trailing* total vanishes instead of rolling over: a December with no sales loses the preceding eleven months of trailing revenue, rather than reporting them | guarded | the row is still absent until time spines (Phase 2), but the answer names it in `missing_periods` (`tests/test_engine.py::TestAnswers::test_a_trailing_total_reports_the_anchor_it_dropped`; over generated data, `tests/test_engine.py::test_every_expected_period_is_returned_or_reported_missing`) |
| An empty period read as not asked for | August had no orders, so it is simply not in the rows | guarded | same: `missing_periods` on every answer with a grain |
| Anchors narrowed by a dimension | Building anchor periods per dimension value means a cut with no rows that month stops anchoring, and its trailing total disappears | guarded | `tests/test_compiler.py::TestCumulativeMetrics::test_anchors_are_never_grouped_by_a_dimension` |
| Range join fan-out at fine grains | A daily trailing-365 window replicates each row up to 365 times | unguarded | measured, not assumed: `tests/test_compiler.py::TestCumulativeAgainstDuckDB::test_row_multiplication_at_daily_grain_is_bounded_by_the_window`; cost is revisited with real volumes in 1.7 |
| Partition column ≠ business date | Bounding the wrong column drops or double-counts edge rows | guarded | `tests/test_compiler.py::TestDateBounds::test_a_late_arriving_table_bounds_both_columns`; the manifest must declare the lag |
| Week boundaries differ per engine | Weekly figures shift by a day between warehouses | unguarded | 1.7 divergence catalog |
| Timezone handling | A day boundary moves; daily metrics double-count or drop rows | unguarded | Phase 2 |

## Request handling

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Ambiguous dimension name | The wrong table's `region` answers the question | guarded | `tests/test_validate.py::TestRefusals::test_ambiguous_bare_dimension_lists_qualified_names` |
| Metric filter placed in `WHERE` | Filtering before aggregation answers a different question | guarded | `tests/test_validate.py::TestResolution::test_filters_split_by_what_the_field_is` |
| Enforced rule that the signature never advertised | The agent wastes turns discovering constraints | guarded | `tests/test_signature.py::test_every_rule_contributes_to_the_signature` |
| Undocumented error code | Agents branch on a code no document describes | guarded | `tests/test_error_codes.py` |
| A constraint that only fixtures exercise | Real manifests differ from the ones we wrote | guarded | `tests/test_properties.py` — invariants over generated manifests |
| A requirement the signature implies but never states | An agent that read the signature is still refused (found by the property suite: a snapshot metric demanded a grain it never declared) | guarded | `tests/test_properties.py::test_a_metric_that_demands_a_grain_says_so` |
| A question whose words appear nowhere in the manifest | Discovery returns nothing and the agent gives up or invents a metric | guarded | the user is asked which metric they mean (`tests/test_server.py::test_a_question_the_catalog_cannot_answer_is_put_to_the_user`), or the catalog is returned with `no_match`; misses are recorded |
| A client that cannot ask its user | The clarifying question never reaches a person, and an agent may answer it itself | unguarded | inherent to the protocol: the SDK notes an agent client may satisfy an elicitation without asking. The `no_match` catalog reply is the floor |
| A certified definition that cannot be written down | "Revenue" really means revenue excluding refunds; without metric filters the manifest states something else | guarded | `tests/test_manifest_valid.py::test_metric_level_filters_are_part_of_the_definition` |
| A metric filter naming a cut the metric cannot reach | The definition would silently not apply | guarded | `tests/test_manifest_invalid.py` (`metric-filter-on-unknown-field`) |
| Filtering a field the definition already pins | "Store revenue" from a web-only metric returns 0, reading as "stores sold nothing" | guarded | `tests/test_validate.py::TestDefinitionConstraints` |
| Asking for what a definition excludes | Refunds from a metric defined as "excluding refunds" returns 0, reading as "no refunds" | guarded | same — refused as `contradictory_filter`, pointing at a metric that can answer |
| A contradiction behind `LIKE` or a range | Same empty-reads-as-zero failure, where disjointness cannot be proved | unguarded | deliberate: an unreliable refusal is its own failure. The constraint is disclosed in the signature |

## Execution and cost

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Agent-supplied SQL text | Prompt injection reaches the query | refused | agents send structured requests only; no raw-SQL path, and no tool accepts one (`tests/test_server.py::test_no_tool_accepts_sql`) |
| Filter value interpolated into SQL | Injection through a value | guarded | `tests/test_compiler.py::TestFilters::test_values_are_bound_never_interpolated` |
| Compiler bug that drops a bound or reaches a new table | Generated SQL is valid and wrong | guarded | the statement is re-parsed and asserted before execution (`tests/test_guardrail.py::TestMutants`), and execution cannot skip it (`tests/test_engine.py::TestTheGuardrailRunsFirst`) |
| Row order left to the engine | The limit keeps different rows on different engines, or NULL sorts first on one | guarded | results are totally ordered, NULL last (`tests/test_compiler.py::TestRowOrder`) |
| A partial answer presented as whole | Rows past a cap are dropped silently, or periods beyond the limit are called missing | guarded | the row cap refuses rather than truncates (`tests/test_engine.py::TestTheConnection::test_no_answer_is_ever_larger_than_the_row_cap`); nothing is claimed when the limit is reached (`tests/test_engine.py::TestAnswers::test_nothing_is_claimed_about_rows_the_limit_cut_off`) |
| Money rounded through a float | £100.50 + £49.50 comes back near £150, not at it | guarded | exact decimals are returned as strings (`tests/test_engine.py::TestAnswers::test_rows_are_named_by_the_compiler_and_decimals_stay_exact`) |
| A runaway statement | One request holds the warehouse indefinitely | guarded | `tests/test_engine.py::TestTheConnection::test_a_slow_statement_is_stopped_at_the_deadline`, and the deadline stops only that statement (`test_a_timeout_stops_only_its_own_statement`) |
| Values in a driver error | DuckDB quotes the bound value that failed to convert | guarded | fixed message to the agent; the log gets the error class and the statement only (`tests/test_engine.py::TestTheConnection::test_a_driver_error_carries_no_values_to_the_agent_or_the_log`) |
| Compiler bug the guardrails cannot see | Generated SQL is valid, bounded, and still returns a wrong number | unguarded | the guardrails check shape, not arithmetic; 1.7 conformance and the 1.8 differential soak are what catch this |
| Same request, different answer per engine | Two warehouses disagree and nobody notices | unguarded | 1.7 parity matrix |
| Result values in logs or traces | Observability becomes a data leak | unguarded | telemetry never records values (Phase 4 enforces) |
