# Glossary

The vocabulary of the MetricBridge manifest. Terms follow dbt MetricFlow where the concept is the
same, so a dbt project maps onto them directly.

## Manifest

**Manifest** — the set of YAML files declaring every semantic model and metric the gateway admits.
A metric absent from the manifest cannot be queried: the manifest is a whitelist.

**Manifest version** — a SHA-256 hash of the manifest's meaning. It changes when a definition
changes and stays the same when only formatting, comments or file layout change. Answers carry the
version that produced them.

## Semantic models

**Semantic model** — one warehouse table and everything that can honestly be asked of it: its
entities, dimensions and measures.

**Entity** — a join key. Its type decides how tables may join:

| Type | Meaning |
| --- | --- |
| `primary` | Unique in this table; at most one per semantic model |
| `unique` | Unique in this table |
| `foreign` | Refers to an entity that is primary or unique elsewhere; may repeat |

`natural` entities (slowly changing dimensions) are not supported in this version.

**Join cardinality** — derived from entity types, never declared:

| From → to | Cardinality | Admitted |
| --- | --- | --- |
| `foreign` → `primary` / `unique` | many-to-one | yes |
| `primary` / `unique` ↔ `primary` / `unique` | one-to-one | yes |
| `foreign` ↔ `foreign`, with no unique side | many-to-many | no — it fans out and double-counts |

**Unreconciled** — rows a join could not match: an empty key, or a key naming a row the joined
table does not have. An answer reports them per joined entity as `rows`, `empty_key_rows` and the
metric's `value` over just those rows, counted over the whole scan before grouping and the row
limit.

**Dimension** — a cut. `categorical` dimensions group by value; `time` dimensions carry a
**time granularity**.

**Time granularity** — `day`, `week`, `month`, `quarter` or `year`. Finer granularities are not
supported in this version.

**Aggregation time dimension** — the business date a measure is aggregated by, when that is not the
partition column: a table partitioned on ingestion date is still measured by when the order
happened. Declaring one requires **partition lag** (`partition_lag_days`), how far the partition may
trail the business date, so the scan stays bounded without dropping late-arriving rows.

**Partition dimension** — the one time dimension per semantic model marked `is_partition: true`.
Every query is bounded on it, so no scan is ever unbounded. A semantic model with measures must
have exactly one.

**Grain** — what one row of a table means, such as one order line or one product per day. Joining
tables of different grain without a unique key multiplies rows.

## Measures

**Measure** — an aggregation over an expression: `sum`, `count`, `count_distinct`, `min`, `max` or
`average`. `percentile`, `median` and `sum_boolean` are not supported in this version. A measure is
**additive** unless it declares `additive: false`.

**Additivity** — whether a measure may be summed along a dimension. Revenue is additive across
time; units on hand are not, because summing daily snapshots counts the same stock once per day.

**Non-additive dimension** — declared on a snapshot measure to say how it rolls up across a time
dimension: take the value at that dimension's `min` or `max` (the **window choice**) within each
group, instead of summing. **Window groupings** name the entities the choice is made per, for
example the last snapshot per product. Declaring it requires `additive: false`. A measure marked
`additive: false` *without* this declaration is refused when a query would sum it across time —
only its base grain is offered.

## Metrics

**Metric** — the governed name an agent asks for, with a description, an owner and a **tier**
(`certified`, `experimental` or `deprecated`). A deprecated metric still answers, with a notice
naming its successor (`replaced_by`), and discovery hides it by default.

**Metric filter** — part of a metric's definition rather than of a request: `web_revenue` means
revenue *where channel is web*. Declared structurally (`field`, `operator`, `value`), never as SQL,
so the compiler builds the syntax and the driver carries the value. It applies to every query of
that metric, is checked when the manifest loads, and is disclosed in the signature so an agent can
see what a metric permanently excludes.

**Qualified dimension name** — `entity__dimension`, such as `customer__region`, naming a cut
reached through a join. A bare name is accepted while it points at exactly one cut; when two
reachable tables share it, the request is refused with the qualified names offered.

| Type | Definition |
| --- | --- |
| `simple` | One measure |
| `ratio` | A `numerator` metric divided by a `denominator` metric, each simple or cumulative |
| `cumulative` | A measure accumulated over a trailing **window** (such as `12 months`) or to date within a period (**grain to date**, such as `month`) |

`derived` and `conversion` metrics are not supported in this version.
