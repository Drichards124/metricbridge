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
| Snapshot rolled up the wrong way | Month-end stock computed as an average, or from the first day | guarded | declaration is explicit (`window_choice`); `tests/test_validate.py::TestResolution::test_declared_snapshot_rollup_is_admitted` |
| Grain finer than the table | A weekly table asked for a daily figure invents rows | guarded | `tests/test_validate.py::test_a_grain_finer_than_the_table_is_refused` |
| `count_distinct` rolled up | Distinct orders per day summed into a month double-counts repeat orders | unguarded | 1.4 recomputes from base rows (D11) |
| `average` rolled up | An average of daily averages is not the period average | unguarded | 1.4 recomputes from base rows (D11) |
| `percentile` / `median` | Not additive, and engines disagree on method | refused | not supported in this version (D9) |

## Joins

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Many-to-many join | Rows fan out and every measure inflates | refused | `tests/test_manifest_invalid.py` (`many-to-many-through-shared-foreign-entity`) |
| Inner join to a dimension | Rows with a null key vanish, shrinking the denominator | unguarded | 1.4 joins LEFT and reports null-key counts |
| Fan trap (two facts, one shared dimension) | One fact's measure multiplies by the other's row count | unguarded | 1.7 corpus (D11) |
| Chasm trap (two unrelated facts joined through a dimension) | A cartesian product presented as a total | unguarded | 1.7 corpus (D11) |
| Declared key is not actually unique | A "N:1" join fans out because the data disagrees with the manifest | unguarded | verified against the warehouse in 1.6–1.7 |
| SCD type 2 dimension | Joining without validity windows attributes history to the current row | refused | `natural` entities not supported (D9); Phase 2 |

## Time

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Unbounded scan | No date bound: cost and blast radius unbounded | guarded | `tests/test_validate.py::TestRefusals::test_missing_date_range` |
| Inclusive end date on a timestamp column | The final day is silently dropped | unguarded | 1.4 compiles half-open `[start, end + 1 day)`; spike defect 2 |
| Trailing window not widened | A trailing-12-month metric pruned to the output window returns a wrong number | unguarded | 1.4 widens by the declared lookback |
| Partition column ≠ business date | Bounding the wrong column drops or double-counts edge rows | unguarded | 1.4 bounds both (D11) |
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
| A question whose words appear nowhere in the manifest | Discovery returns nothing and the agent gives up or invents a metric | unguarded | deliberate: recorded by `tests/test_discovery.py::test_a_phrasing_sharing_no_word_with_the_catalog_finds_nothing`. The trigger for embeddings is measured recall failure |
| A certified definition that cannot be written down | "Revenue" really means revenue excluding refunds; without metric filters the manifest states something else | guarded | `tests/test_manifest_valid.py::test_metric_level_filters_are_part_of_the_definition` |
| A metric filter naming a cut the metric cannot reach | The definition would silently not apply | guarded | `tests/test_manifest_invalid.py` (`metric-filter-on-unknown-field`) |
| Filtering a field the definition already pins | "Store revenue" from a web-only metric returns 0, reading as "stores sold nothing" | guarded | `tests/test_validate.py::TestDefinitionConstraints` |
| Asking for what a definition excludes | Refunds from a metric defined as "excluding refunds" returns 0, reading as "no refunds" | guarded | same — refused as `contradictory_filter`, pointing at a metric that can answer |
| A contradiction behind `LIKE` or a range | Same empty-reads-as-zero failure, where disjointness cannot be proved | unguarded | deliberate: an unreliable refusal is its own failure. The constraint is disclosed in the signature |

## Execution and cost

| Failure | What goes wrong | Status | Guard |
| --- | --- | --- | --- |
| Agent-supplied SQL text | Prompt injection reaches the query | refused | agents send structured requests only; no raw-SQL path |
| Filter value interpolated into SQL | Injection through a value | unguarded | 1.4 binds every value as a parameter |
| Compiler bug that no rule catches | Generated SQL is valid and wrong | unguarded | 1.5 re-parses and asserts on the AST; 1.8 differential soak |
| Same request, different answer per engine | Two warehouses disagree and nobody notices | unguarded | 1.7 parity matrix |
| Result values in logs or traces | Observability becomes a data leak | unguarded | telemetry never records values (Phase 4 enforces) |
