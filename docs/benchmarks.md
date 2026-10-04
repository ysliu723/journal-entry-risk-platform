# Benchmarks

Produced by `python -m scripts.benchmark` on the default synthetic ledger. One laptop, one run of the script: treat the numbers as rough, and compare rows with each other rather than with other machines.

- Date: 2026-10-03
- Machine: Apple M1 Pro, macOS-26.6.2-arm64-arm-64bit
- Python 3.12.15, PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) (in Docker, same machine)
- Data: 100,000 entries, 204,548 lines, 3,519 rule hits

## Pipeline (no database)

| Stage | Seconds |
|---|---|
| Parse and validate the GL CSV | 1.79 |
| Integrity checks (incl. TB rollforward) | 0.18 |
| Score and rank with 11 rules | 0.42 |

Peak memory of the Python process: 254 MB.

## Saving a run (entries, lines, and rule hits)

| Method | Seconds | Rows per second |
|---|---|---|
| orm | 26.92 | 11,444 |
| core | 6.09 | 50,589 |
| copy | 2.85 | 107,940 |

## Listing entries (3 runs, 300,000 entries in the table)

Median and 95th percentile of 30 executions after warm-up, plus the plan from EXPLAIN ANALYZE.

| Index | Query | p50 ms | p95 ms | Plan |
|---|---|---|---|---|
| A. No index for the list | HIGH entries, page 1 | 13.75 | 20.24 | Gather Merge > Sort > Parallel Seq Scan |
| A. No index for the list | All entries, page 1 | 11.73 | 12.64 | Gather Merge > Sort > Parallel Seq Scan |
| A. No index for the list | LOW entries, page 1 | 15.70 | 19.82 | Gather Merge > Sort > Parallel Seq Scan |
| A. No index for the list | Count HIGH entries | 13.07 | 15.01 | Finalize Aggregate > Gather > Partial Aggregate > Parallel Seq Scan |
| B. (risk_level) | HIGH entries, page 1 | 1.07 | 2.10 | Sort > Index Scan |
| B. (risk_level) | All entries, page 1 | 11.70 | 17.66 | Gather Merge > Sort > Parallel Seq Scan |
| B. (risk_level) | LOW entries, page 1 | 15.12 | 16.25 | Gather Merge > Sort > Parallel Seq Scan |
| B. (risk_level) | Count HIGH entries | 0.60 | 1.70 | Aggregate > Index Scan |
| C. (run_id, risk_level, risk_score DESC, entry_id, id) | HIGH entries, page 1 | 1.02 | 1.36 | Index Scan |
| C. (run_id, risk_level, risk_score DESC, entry_id, id) | All entries, page 1 | 11.52 | 20.87 | Gather Merge > Sort > Parallel Seq Scan |
| C. (run_id, risk_level, risk_score DESC, entry_id, id) | LOW entries, page 1 | 0.99 | 1.27 | Index Scan |
| C. (run_id, risk_level, risk_score DESC, entry_id, id) | Count HIGH entries | 0.59 | 0.75 | Aggregate > Index Only Scan |
| D. (run_id, risk_score DESC, entry_id, id) | HIGH entries, page 1 | 1.03 | 1.85 | Index Scan |
| D. (run_id, risk_score DESC, entry_id, id) | All entries, page 1 | 0.98 | 1.37 | Index Scan |
| D. (run_id, risk_score DESC, entry_id, id) | LOW entries, page 1 | 1.03 | 2.52 | Index Scan |
| D. (run_id, risk_score DESC, entry_id, id) | Count HIGH entries | 12.90 | 14.85 | Finalize Aggregate > Gather > Partial Aggregate > Parallel Seq Scan |
