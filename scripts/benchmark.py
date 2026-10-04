"""Measure where the time goes, which load method is fastest, and which index serves the entry list.

    docker compose up -d db
    python -m scripts.generate_synthetic_data
    python -m scripts.benchmark

Writes docs/benchmarks.md. Uses the je_risk_bench database and drops its tables.
"""

import argparse
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import load_config
from app.db.models import Base
from app.db.persistence import LOAD_METHODS, save_run
from app.engine.risk_engine import RiskEngine
from app.ingestion.csv_loader import load_gl_detail, load_trial_balance
from app.integrity.checks import run_all_checks
from app.rules import default_rules
from app.services.analysis import RunAnalysis

BENCH_DATABASE_URL = os.environ.get(
    "BENCH_DATABASE_URL", "postgresql+psycopg://je_risk:je_risk@localhost:5432/je_risk_bench"
)
RUNS_FOR_QUERIES = 3  # several runs in the table, so "WHERE run_id = ..." has to find one among others
QUERY_REPEATS = 30

ORDER = "ORDER BY risk_score DESC, entry_id, id LIMIT 50"
QUERIES = {
    "HIGH entries, page 1": f"SELECT * FROM journal_entries WHERE run_id = :run_id AND risk_level = 'HIGH' {ORDER}",
    "All entries, page 1": f"SELECT * FROM journal_entries WHERE run_id = :run_id {ORDER}",
    "LOW entries, page 1": f"SELECT * FROM journal_entries WHERE run_id = :run_id AND risk_level = 'LOW' {ORDER}",
    "Count HIGH entries": "SELECT count(*) FROM journal_entries WHERE run_id = :run_id AND risk_level = 'HIGH'",
}
INDEX_VARIANTS = {
    "A. No index for the list": None,
    "B. (risk_level)": "(risk_level)",
    "C. (run_id, risk_level, risk_score DESC, entry_id, id)": "(run_id, risk_level, risk_score DESC, entry_id, id)",
    "D. (run_id, risk_score DESC, entry_id, id)": "(run_id, risk_score DESC, entry_id, id)",
}
# Indexes from app/db/models.py that would serve these queries; dropped so each variant is tested alone.
LIST_INDEXES = ("ix_journal_entries_run_level_score", "ix_journal_entries_run_score", "ix_journal_entries_run_entry_id")


def stage_timings(data_dir: Path) -> tuple[RunAnalysis, list[tuple[str, float]], float]:
    config = load_config(data_dir / "client_config.json")
    timings = []

    start = time.perf_counter()
    load = load_gl_detail(data_dir / "gl_detail.csv")
    timings.append(("Parse and validate the GL CSV", time.perf_counter() - start))

    start = time.perf_counter()
    trial_balance = load_trial_balance(data_dir / "trial_balance.csv")
    findings = run_all_checks(load.entries, trial_balance)
    timings.append(("Integrity checks (incl. TB rollforward)", time.perf_counter() - start))

    start = time.perf_counter()
    engine = RiskEngine(default_rules(), config)
    results = engine.score_population(load.entries)
    timings.append(("Score and rank with 11 rules", time.perf_counter() - start))

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS, kilobytes on Linux
    peak_mb = peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024
    analysis = RunAnalysis(config, load, trial_balance, findings, results, engine.rule_set_version)
    return analysis, timings, peak_mb


def reset_schema(engine) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def load_timings(engine, analysis: RunAnalysis) -> list[tuple[str, float]]:
    timings = []
    for method in LOAD_METHODS:
        reset_schema(engine)
        print(f"  loading with {method} ...", flush=True)
        with Session(engine) as session:
            start = time.perf_counter()
            save_run(session, analysis, "gl_detail.csv", "trial_balance.csv", method=method)
            session.commit()
            timings.append((method, time.perf_counter() - start))
    return timings


def plan_summary(plan_lines: list[str]) -> str:
    """'Limit -> Sort -> Seq Scan on journal_entries' becomes 'Sort > Seq Scan'."""
    nodes = []
    for number, line in enumerate(plan_lines):
        stripped = line.strip()
        if number > 0 and not stripped.startswith("->"):
            continue  # detail lines such as "Sort Key: ..." or "Filter: ..."
        name = stripped.removeprefix("->").strip().split("  (")[0]
        if any(word in name for word in ("Scan", "Sort", "Gather", "Aggregate")):
            nodes.append(name.replace(" on journal_entries", "").replace("using bench_ix", "").strip())
    return " > ".join(nodes)


def query_timings(engine, analysis: RunAnalysis) -> list[list[str]]:
    reset_schema(engine)
    for _ in range(RUNS_FOR_QUERIES):
        with Session(engine) as session:
            save_run(session, analysis, "gl_detail.csv", "trial_balance.csv")
            session.commit()
    run_id = 2  # the middle run

    rows = []
    with engine.connect() as connection:
        for name in LIST_INDEXES:
            connection.execute(text(f"DROP INDEX {name}"))
        connection.commit()
        for variant, columns in INDEX_VARIANTS.items():
            connection.execute(text("DROP INDEX IF EXISTS bench_ix"))
            if columns:
                connection.execute(text(f"CREATE INDEX bench_ix ON journal_entries {columns}"))
            connection.execute(text("ANALYZE journal_entries"))
            connection.commit()
            for query_name, sql in QUERIES.items():
                params = {"run_id": run_id}
                for _ in range(3):  # warm up the cache
                    connection.execute(text(sql), params).fetchall()
                samples = []
                for _ in range(QUERY_REPEATS):
                    start = time.perf_counter()
                    connection.execute(text(sql), params).fetchall()
                    samples.append((time.perf_counter() - start) * 1000)
                plan = [row[0] for row in connection.execute(text("EXPLAIN ANALYZE " + sql), params)]
                p95 = statistics.quantiles(samples, n=20)[-1]
                rows.append([variant, query_name, f"{statistics.median(samples):.2f}", f"{p95:.2f}", plan_summary(plan)])
    return rows


def environment(engine) -> list[str]:
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    with engine.connect() as connection:
        postgres = connection.execute(text("SHOW server_version")).scalar()
    return [
        f"- Date: {date.today().isoformat()}",
        f"- Machine: {cpu}, {platform.platform()}",
        f"- Python {platform.python_version()}, PostgreSQL {postgres} (in Docker, same machine)",
    ]


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the pipeline, load methods, and indexes.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--out", type=Path, default=Path("docs/benchmarks.md"))
    args = parser.parse_args()
    engine = create_engine(BENCH_DATABASE_URL)

    print("Timing the pipeline ...", flush=True)
    analysis, stages, peak_mb = stage_timings(args.data)
    entry_count = len(analysis.results)
    line_count = sum(len(result.entry.lines) for result in analysis.results)
    hit_count = sum(len(result.hits) for result in analysis.results)
    row_count = entry_count + line_count + hit_count

    print("Timing the load methods ...", flush=True)
    loads = load_timings(engine, analysis)

    print("Timing the list queries ...", flush=True)
    queries = query_timings(engine, analysis)

    report = "\n".join([
        "# Benchmarks",
        "",
        "Produced by `python -m scripts.benchmark` on the default synthetic ledger. "
        "One laptop, one run of the script: treat the numbers as rough, and compare rows with each other "
        "rather than with other machines.",
        "",
        *environment(engine),
        f"- Data: {entry_count:,} entries, {line_count:,} lines, {hit_count:,} rule hits",
        "",
        "## Pipeline (no database)",
        "",
        markdown_table(["Stage", "Seconds"], [[name, f"{seconds:.2f}"] for name, seconds in stages]),
        "",
        f"Peak memory of the Python process: {peak_mb:,.0f} MB.",
        "",
        "## Saving a run (entries, lines, and rule hits)",
        "",
        markdown_table(
            ["Method", "Seconds", "Rows per second"],
            [[method, f"{seconds:.2f}", f"{row_count / seconds:,.0f}"] for method, seconds in loads],
        ),
        "",
        f"## Listing entries ({RUNS_FOR_QUERIES} runs, {entry_count * RUNS_FOR_QUERIES:,} entries in the table)",
        "",
        f"Median and 95th percentile of {QUERY_REPEATS} executions after warm-up, plus the plan from EXPLAIN ANALYZE.",
        "",
        markdown_table(["Index", "Query", "p50 ms", "p95 ms", "Plan"], queries),
        "",
    ])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
