Percentiles are over requests that received a first token; given-up requests count in 'Timed out or given up'.

### Table 2a

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
|---|---|---|---|---|---|---|
| real_steady | real | 66 | 1266 | 3 | 1141 / 2201 / 2703 | 1141 / 2205 / 2707 |
| real_burst_cold_index | real | 8 | 4 | 8 at once | 3482 / 4013 / 4041 | 3492 / 4023 / 4051 |
| real_burst | real | 8 | 1 | 8 at once | 920 / 1155 / 1211 | 925 / 1162 / 1219 |
| steady | stand-in | 71 | 181 | 24 | 1847 / 2503 / 2615 | 2247 / 2918 / 3037 |
| burst | stand-in | 100 | 8 | 100 at once | 2065 / 4619 / 6948 | 2198 / 5047 / 7384 |
| overload | stand-in | 376 | 185 | 125 | 2026 / 6264 / 7347 | 2401 / 6424 / 7769 |
| overload_naive_retry | stand-in | 376 | 187 | 125 | 2241 / 6971 / 7708 | 2622 / 7112 / 7832 |
| overload_budget_retry | stand-in | 376 | 184 | 125 | 2176 / 6401 / 7312 | 2514 / 6664 / 7621 |
| halved_limit | stand-in | 71 | 180 | 24 | 1830 / 2414 / 2817 | 2238 / 2836 / 3237 |
| daily_exhausted | stand-in | 50 | 109 | 27 | 5 / 1920 / 2059 | 5 / 2334 / 2466 |

### Table 2b

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
|---|---|---|---|---|---|---|---|
| real_steady | 98.5 | 0.0 | 1.5 | 0.00 | 0 | 1733 | $0.30 |
| real_burst_cold_index | 50.0 | 0.0 | 50.0 | 0.00 | 7 | 1188 | $0.21 |
| real_burst | 100.0 | 0.0 | 0.0 | 0.00 | 7 | 955 | $0.19 |
| steady | 98.6 | 0.0 | 1.4 | 0.00 | 4 | 1050 | $0.21 |
| burst | 100.0 | 0.0 | 0.0 | 0.00 | 99 | 587 | $0.12 |
| overload | 92.3 | 5.3 | 2.4 | 0.01 | 23 | 938 | $0.15 |
| overload_naive_retry | 59.0 | 0.0 | 41.0 | 0.05 | 27 | 845 | $0.17 |
| overload_budget_retry | 90.7 | 7.7 | 1.6 | 0.08 | 23 | 925 | $0.15 |
| halved_limit | 100.0 | 0.0 | 0.0 | 0.00 | 3 | 1050 | $0.21 |
| daily_exhausted | 30.0 | 70.0 | 0.0 | 0.00 | 3 | 1048 | $0.21 |

### Table 3 (real Groq, scenario real_steady)

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|
| 120w-k4-bm25 | 22 | 59.1 | 4.5 | 27.3 | 1019 | 1022 / 2507 | 1040 / 2512 |
| 250w-k4-bm25 | 22 | 54.5 | 4.5 | 27.3 | 1776 | 1104 / 2016 | 1125 / 2016 |
| 120w-k4-rerank | 22 | 54.5 | 0.0 | 27.3 | 2412 | 1459 / 2212 | 1460 / 2217 |
