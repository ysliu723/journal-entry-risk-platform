# Rule Evaluation

Synthetic ledger: 100,000 entries, 200 injected risk scenarios (0.2%). Generated with `python -m scripts.generate_synthetic_data` (default seed 42); produced by `python -m evaluation.evaluate_rules`.

Synthetic data shows how the rules behave on a known population. It is not evidence of detection rates on real ledgers.

## Coverage by scenario

| Scenario | Entries | Ranked HIGH | Ranked MEDIUM or HIGH |
|---|---|---|---|
| FICTITIOUS_VENDOR | 40 | 0.0% | 0.0% |
| SELF_APPROVED_PAYMENT | 30 | 0.0% | 100.0% |
| SUSPENSE_ACCOUNT | 20 | 50.0% | 100.0% |
| THRESHOLD_AVOIDANCE | 50 | 44.0% | 100.0% |
| YEAR_END_REVENUE_TOPSIDE | 60 | 88.3% | 100.0% |
| **All injected** | 200 | 42.5% | 80.0% |

## Alert volume

| Level | Alerts | Injected | Precision | False alarms | False alarm rate |
|---|---|---|---|---|---|
| HIGH | 89 | 85 | 95.5% | 4 | 0.004% |
| MEDIUM or HIGH | 335 | 160 | 47.8% | 175 | 0.175% |

False alarm rate = false alarms / all normal entries.

## If the team can only review the top K

| K | Injected in top K | Precision@K | Recall@K |
|---|---|---|---|
| 50 | 50 | 100.0% | 25.0% |
| 100 | 94 | 94.0% | 47.0% |
| 200 | 138 | 69.0% | 69.0% |
| 500 | 160 | 32.0% | 80.0% |

## Rule firing rates

| Rule | Fires on injected | Fires on normal manual | Normal manual hits |
|---|---|---|---|
| WEEKEND_ENTRY | 8.0% | 3.1% | 311 |
| LATE_NIGHT_ENTRY | 14.0% | 1.1% | 105 |
| ROUND_AMOUNT | 21.0% | 2.9% | 290 |
| NEAR_APPROVAL_THRESHOLD | 25.5% | 0.1% | 10 |
| MISSING_APPROVAL | 23.0% | 0.0% | 4 |
| SELF_APPROVAL | 20.0% | 0.0% | 0 |
| PERIOD_END_ENTRY | 26.5% | 3.7% | 365 |
| POST_CLOSE_ENTRY | 8.0% | 0.0% | 0 |
| VAGUE_DESCRIPTION | 28.0% | 18.8% | 1871 |
| SELDOM_USED_ACCOUNT | 10.0% | 0.0% | 4 |
| INFREQUENT_PREPARER | 27.5% | 0.0% | 4 |
