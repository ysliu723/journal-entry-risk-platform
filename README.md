# Journal Entry Risk Platform

A backend system that ingests a company's general-ledger journal entries, checks
that the population is complete, and ranks every entry for audit review with
configurable, explainable risk rules.

**Python · FastAPI · PostgreSQL · SQLAlchemy · Docker · pytest**

[![tests](https://github.com/ysliu723/journal-entry-risk-platform/actions/workflows/tests.yml/badge.svg)](https://github.com/ysliu723/journal-entry-risk-platform/actions/workflows/tests.yml)

## Why

Auditing standards require auditors to test journal entries for signs of
management override of controls: PCAOB AS 2401 for public companies, AICPA
AU-C 240 for private companies, and ISA 240 internationally. A mid-size company posts
hundreds of thousands of entries a year, so testing means filtering. In practice
the hard parts are:

1. **Is the data complete?** Testing an export that is missing entries gives
   false comfort. Auditors first reconcile the general ledger to the trial balance.
2. **Too many alerts.** Simple filters flag thousands of entries, more than a
   team can review. Ranking matters more than flagging.
3. **Why was it flagged?** Every point of a risk score has to be traceable to a
   rule and a reason, and the result has to be reproducible later.

This project is built around those three needs.

## What it does

- **Integrity checks before scoring.** Balanced entries, duplicate entry IDs,
  gaps in the entry number sequence, and a trial-balance rollforward
  (opening balance + GL activity = closing balance, for every account).
- **11 risk rules** that map to the characteristics of potentially fraudulent
  journal entries described in AS 2401 / AU-C 240: timing, amounts, approvals,
  descriptions, and rules that compare an entry with the whole population.
- **Ranked, explained results.** Each entry gets a score, a level
  (LOW / MEDIUM / HIGH), and the list of rules that fired with a reason for each.
- **Reproducible runs.** Each upload is stored as an unchanging run together with
  its client configuration and the version of every rule.
- **REST API** to upload a run, filter and sort its entries, and explain any entry.

## Results

Measured on a laptop with a synthetic ledger of 100,000 entries (204,548 lines).
Full tables: [docs/benchmarks.md](docs/benchmarks.md) and
[docs/evaluation.md](docs/evaluation.md).

| | |
|---|---|
| Load, check, and score 100,000 entries (no database) | 2.4 s (parse 1.8 s, checks 0.2 s, scoring 0.4 s) |
| Upload the same run through the API, saved to PostgreSQL | 5.6 s end to end |
| Saving a run: PostgreSQL COPY vs ORM inserts | 2.9 s vs 26.9 s |
| Entry list query, 300,000 entries in the table | ~1 ms with the right index, 11–16 ms without |

On 200 injected risk scenarios (0.2% of the ledger):

| | |
|---|---|
| Precision of the top 50 / top 100 ranked entries | 100% / 94% |
| Injected entries ranked HIGH / MEDIUM or HIGH | 42.5% / 80% |
| Normal entries ranked HIGH (false alarms) | 4 of 99,800 |
| Look-alike vendor invoices found | 0% (by design: no rule can see them; see Limitations) |

The data is synthetic, so these numbers show how the rules behave on a known
population, not detection rates on real ledgers.

## Quick start

### Everything in Docker

```bash
docker compose up --build
```

Then open http://localhost:8000/docs for the interactive API, or upload the
sample ledger:

```bash
curl -X POST localhost:8000/runs \
  -F gl_detail=@data/sample/gl_detail.csv \
  -F trial_balance=@data/sample/trial_balance.csv \
  -F config=@data/sample/client_config.json

curl "localhost:8000/runs/1/entries?risk_level=HIGH"
```

### Local development

Requires Python 3.12+ and Docker (for PostgreSQL).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up -d db          # PostgreSQL, with test and benchmark databases
pytest                           # 141 tests; database tests are skipped if PostgreSQL is not running
                                 # (GitHub Actions runs all of them against PostgreSQL on every push)

# Command-line report, no database needed
python -m app.cli data/sample/gl_detail.csv data/sample/trial_balance.csv \
    --config data/sample/client_config.json

# API with auto-reload
uvicorn app.main:app --reload
```

### Synthetic data, evaluation, benchmarks

```bash
python -m scripts.generate_synthetic_data --entries 100000 --defects   # writes data/generated/
python -m evaluation.evaluate_rules                                     # writes docs/evaluation.md
python -m scripts.benchmark                                             # writes docs/benchmarks.md
```

## How it works

```text
GL detail CSV + trial balance CSV + client config (JSON)
  │
  ├─ ingestion    parse rows; reject malformed ones with their line numbers
  ├─ integrity    balanced entries, duplicate IDs, sequence gaps, TB rollforward
  ├─ rules        11 rules; population rules use statistics computed once per run
  ├─ engine       add up the scores, assign levels, rank highest risk first
  │
  ├─ CLI          print the report
  └─ API          save the run to PostgreSQL; filter, sort, page, explain
```

| Folder | Responsibility |
|---|---|
| `app/domain` | Journal entry (header + lines) and trial balance. No framework code. |
| `app/ingestion` | CSV loading and validation. |
| `app/integrity` | Data integrity checks. |
| `app/rules` | The rule interface and the 11 rules. |
| `app/engine` | Scoring, risk levels, ranking. |
| `app/config.py` | Per-client settings: fiscal year end, close date, thresholds, weights. |
| `app/services` | The whole pipeline in one call, shared by the CLI and the API. |
| `app/db` | Tables, saving a run (ORM / bulk insert / COPY), read queries. |
| `app/api` | FastAPI routes and response models. |
| `scripts`, `evaluation` | Synthetic data generator, benchmarks, rule evaluation. |

### Design decisions

- **A journal entry is a header plus lines.** ERP exports are flat (one row per
  line, header repeated); the loader groups them and checks the header is the same
  on every line.
- **Money is never a float.** `Decimal` in Python, `NUMERIC(18,2)` in PostgreSQL,
  decimal strings in JSON. Amounts are parsed from text without passing through float.
- **Invalid vs. suspicious.** Data that cannot be read (a bad date, a negative
  amount) is rejected and reported with its line number. Data that can be read
  but is wrong (an unbalanced entry, a reused entry ID) is kept and reported as a
  finding, because in an audit that is the finding.
- **Two dates per entry.** `posting_date` is the period the entry affects;
  `created_at` is when it was keyed in. Post-close and backdated entries are only
  visible with both.
- **Most rules apply to manual entries only.** System batch jobs post at night, on
  weekends, at period end, and without a named approver by design. Flagging them
  would bury the real alerts.
- **Runs never change.** Results, the client configuration, and each rule's
  version are stored per run, so any past score can be reproduced and explained.

## Risk rules

| Rule | Fires when (defaults are configurable) | Applies to | Weight |
|---|---|---|---|
| `WEEKEND_ENTRY` | keyed in on a Saturday or Sunday | manual | 10 |
| `LATE_NIGHT_ENTRY` | keyed in at 22:00 or later, or before 06:00 | manual | 20 |
| `PERIOD_END_ENTRY` | posted in the last 3 days of the fiscal year | manual | 20 |
| `POST_CLOSE_ENTRY` | keyed in after the close date but posted into the closed year | all | 30 |
| `ROUND_AMOUNT` | ≥ 10,000 and a multiple of 1,000 | manual | 10 |
| `NEAR_APPROVAL_THRESHOLD` | within 5% below the approval threshold | manual | 30 |
| `MISSING_APPROVAL` | over 50,000 with no approver | manual | 40 |
| `SELF_APPROVAL` | preparer and approver are the same user | all | 40 |
| `VAGUE_DESCRIPTION` | blank, or generic words ("adjustment", "misc", "per CFO") | manual | 10 |
| `SELDOM_USED_ACCOUNT` | uses an account few other entries use | all | 20 |
| `INFREQUENT_PREPARER` | prepared by someone who rarely prepares entries | all | 20 |

Scores add up: 0–29 LOW, 30–59 MEDIUM, 60+ HIGH. A score ranks entries for
review; it is not a probability of fraud.

## API

| Method | Path | |
|---|---|---|
| POST | `/runs` | Upload `gl_detail`, `trial_balance`, and `config`; returns the run with counts and findings |
| GET | `/runs` | All runs, newest first |
| GET | `/runs/{run_id}` | One run: level counts, rejected rows, integrity findings |
| GET | `/runs/{run_id}/entries` | Filter by `risk_level`, `rule_code`, `prepared_by`, `account_number`, `entry_id`; `sort`; `page`, `page_size` |
| GET | `/entries/{id}` | One entry with its lines and every rule hit with its reason |

## Limitations

- **Synthetic data.** The evaluation shows the rules on a population with known
  answers. Real ledgers are messier.
- **Rules cannot see what they do not measure.** Invoices from a look-alike vendor
  ("Northwind Advisory LLC") look normal on every dimension the rules check, so none
  were found. Catching them needs vendor-master analysis or anomaly detection.
- **Some rules are noisy.** The description keyword rule fires on 18.8% of normal
  manual entries (for example "reclass" and "adjustment" are common and legitimate);
  its low weight keeps it from deciding alone.
- **Population thresholds are absolute counts** (e.g. fewer than 50 entries), so
  they must be set per population size; a percentile would transfer better.
- **The whole file is held in memory** (about 250 MB peak for 100,000 entries),
  and uploads are processed during the request.
- **Not production-ready:** no authentication, and tables are created at startup
  instead of with migrations.

## Roadmap

- Background processing for large uploads; deployment to AWS.
- More rules: entries reversed early in the next period (cut-off), Benford's law,
  unusual account combinations.
- Anomaly detection (e.g. Isolation Forest) evaluated against the same scenarios.
- An investigation assistant that gathers evidence for HIGH entries and drafts a
  memo with citations, with a reviewer in the loop. The score stays with the rules.
