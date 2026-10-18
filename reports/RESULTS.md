# Stockout Shield: simulation results

**Synthetic demonstration only. Modelled inventory outcomes, not a clinical study.**

20 seeds × 6 scenarios × 3 policies; 30 days per policy.

Each policy receives identical hidden demand, initial stock, supplier conditions and an INR 20,000 procurement budget. Forecasts see only the policy’s past dispensing. The two forecast policies use the same reorder calculation; only the transfer option differs.

Repeated experiments use the deterministic FEFO-verified allocator. The interactive app also offers a MILP proposal with mandatory safety verification. Model choice is frozen from historical validation; predictions update daily.

| Scenario | Policy | Unmet units | Stockout days | Expired units | Procurement INR | Transfer INR |
|---|---|---:|---:|---:|---:|---:|
| Little transferable surplus | Fixed reorder threshold | 813.2 | 204.9 | 0.0 | 10432.2 | 0.0 |
| Little transferable surplus | Forecast + redistribution | 414.6 | 109.3 | 0.0 | 13866.5 | 0.0 |
| Little transferable surplus | Forecast reorder | 414.6 | 109.3 | 0.0 | 13866.5 | 0.0 |
| Local demand surge | Fixed reorder threshold | 464.8 | 94.0 | 810.6 | 6689.1 | 0.0 |
| Local demand surge | Forecast + redistribution | 37.5 | 9.1 | 606.4 | 9742.5 | 303.6 |
| Local demand surge | Forecast reorder | 209.8 | 34.6 | 810.6 | 9937.5 | 0.0 |
| Normal operations | Fixed reorder threshold | 373.1 | 88.2 | 782.1 | 6447.1 | 0.0 |
| Normal operations | Forecast + redistribution | 18.8 | 6.9 | 580.4 | 9301.0 | 309.2 |
| Normal operations | Forecast reorder | 150.4 | 33.5 | 782.1 | 9654.5 | 0.0 |
| Route disruption | Fixed reorder threshold | 373.1 | 88.2 | 782.1 | 6447.1 | 0.0 |
| Route disruption | Forecast + redistribution | 18.8 | 6.9 | 580.4 | 9301.0 | 309.2 |
| Route disruption | Forecast reorder | 150.4 | 33.5 | 782.1 | 9654.5 | 0.0 |
| Short-dated surplus | Fixed reorder threshold | 433.4 | 101.6 | 1301.8 | 7091.0 | 0.0 |
| Short-dated surplus | Forecast + redistribution | 50.9 | 15.8 | 1189.5 | 10520.1 | 156.2 |
| Short-dated surplus | Forecast reorder | 150.4 | 33.5 | 1301.8 | 10635.5 | 0.0 |
| Supplier delay | Fixed reorder threshold | 1470.5 | 295.6 | 782.1 | 3975.8 | 0.0 |
| Supplier delay | Forecast + redistribution | 118.6 | 35.4 | 421.1 | 8841.7 | 376.0 |
| Supplier delay | Forecast reorder | 446.9 | 101.0 | 782.1 | 8971.0 | 0.0 |

Values are means across seeds. Stockout days count facility–medicine days with unmet demand. Prices are synthetic. Inspect runs.csv for paired seeds, facility_results.csv for local outcomes and daily_results.csv.gz for all dispensing traces.

## Limits

- Synthetic data does not establish performance at real facilities.
- Corrections to stockout-affected consumption are estimates, not observed unmet demand.
- Empirical uncertainty coverage uses few validation windows; ranges are not guarantees.
- Inspect service, waste and cost trade-offs together. No improvement percentage is guaranteed.
- Supplier capacity, cold-chain handling and approval delays are simplified.
- An unforeseen surge becomes visible to policies only through subsequent observed dispensing.
