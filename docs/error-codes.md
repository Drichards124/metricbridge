# Error codes

Every refusal carries a stable `code`, the offending `field` and value, a `remediation`, and the
authorised `valid_alternatives`. Agents branch on the code, so **codes are added, never silently
repurposed**; messages and remediations improve freely. A refusal reply returns *every* problem
found, not just the first.

```json
{
  "ok": false,
  "errors": [
    {
      "code": "ambiguous_dimension",
      "message": "'region' exists in more than one table reachable from 'revenue'.",
      "field": "dimensions",
      "offending_value": "region",
      "remediation": "Name the cut by its entity, as entity__dimension.",
      "valid_alternatives": ["customer__region", "product__region"]
    }
  ]
}
```

| Code | Raised when | Field |
| --- | --- | --- |
| `unknown_metric` | The metric is not in the manifest. Absent means unqueryable. | `metric` |
| `guardrail_violation` | The generated statement failed an assertion on its syntax tree: not a single SELECT, an unbounded scan, an undeclared table, `SELECT *`, or a missing or excessive row limit. | `sql` |
| `no_match` | No governed metric matches the search. The reply carries the catalog: ask the user which they mean rather than guessing. | `query` |
| `unsupported_metric_shape` | The metric combines measures from more than one table, which this version cannot compile. | `metric` |
| `unknown_dimension` | The requested cut is not authorised for this metric. | `dimensions` |
| `ambiguous_dimension` | A bare dimension name exists in more than one reachable table. | `dimensions` |
| `fixed_by_definition` | The metric's definition pins that field to one value, so it is not a cut of this metric. | `dimensions` / `filters` |
| `contradictory_filter` | The filter can never match what the metric's definition allows; the answer would be an empty result that reads like a real zero. | `filters` |
| `unsupported_time_grain` | The requested grain is not a grain this gateway serves. | `time_grain` |
| `missing_partition_filter` | No `date_range` was supplied. An unbounded scan is refused. | `date_range` |
| `invalid_date_range` | A date is not an ISO date, or the range ends before it starts. | `date_range` |
| `partition_window_too_wide` | The range exceeds the metric's `max_window_days`. | `date_range` |
| `non_additive_cut` | A snapshot measure with no declared roll-up would be summed across time. | `time_grain` |
| `unknown_filter_field` | The filter names neither an authorised dimension nor the metric. | `filters` |
| `unsupported_operator` | The filter operator is not one of the supported operators. | `filters` |
| `unknown_order_field` | `order_by` names a field that is not in the result set. | `order_by` |
| `row_limit_exceeded` | The requested `row_limit` is above the server ceiling. | `row_limit` |

Every code above belongs to a rule in the registry (`src/metricbridge/contract/rules.py`), except
`unknown_metric`, which is raised before any rule can run. A test fails if this table and the
registry ever disagree.
