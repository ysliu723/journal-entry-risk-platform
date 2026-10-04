"""Command line: rank journal entries for review, no database needed.

    python -m app.cli data/sample/gl_detail.csv data/sample/trial_balance.csv \
        --config data/sample/client_config.json
"""

import argparse
import sys
from collections import Counter

from app.config import load_config
from app.engine.risk_engine import RiskLevel
from app.services.analysis import RunAnalysis, analyze_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank journal entries for audit review.")
    parser.add_argument("gl_detail", help="GL detail export (CSV, one row per entry line)")
    parser.add_argument("trial_balance", help="trial balance (CSV)")
    parser.add_argument("--config", required=True, help="client config (JSON)")
    parser.add_argument("--top", type=int, default=20, help="how many entries to list (default 20)")
    args = parser.parse_args(argv)

    try:
        analysis = analyze_files(args.gl_detail, args.trial_balance, load_config(args.config))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_report(analysis, args.top)
    return 0


def print_report(analysis: RunAnalysis, top: int) -> None:
    load = analysis.load
    print(f"Loaded {len(load.entries)} entries; rejected {len(load.errors)} invalid row(s).")
    for error in load.errors:
        print(f"  line {error.row_number} ({error.entry_id}): {error.message}")

    print()
    if analysis.findings:
        print(f"Integrity checks: {len(analysis.findings)} finding(s). The population may be incomplete;")
        print("read the ranking below with that caveat.")
        for finding in analysis.findings:
            subject = finding.entry_id or finding.account_number or ""
            print(f"  {finding.check:<18} {subject:<10} {finding.message}")
    else:
        print("Integrity checks: passed.")

    print()
    print(f"{'ENTRY':<10} | {'LEVEL':<6} | {'SCORE':>5} | RULES")
    for result in analysis.results[:top]:
        rules = ", ".join(hit.rule_code for hit in result.hits) or "-"
        print(f"{result.entry_id:<10} | {result.risk_level:<6} | {result.risk_score:>5} | {rules}")

    levels = Counter(result.risk_level for result in analysis.results)
    print()
    print(" | ".join(f"{level} {levels[level]}" for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW)))


if __name__ == "__main__":
    sys.exit(main())
