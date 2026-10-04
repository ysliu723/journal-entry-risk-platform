"""Measure how well the rules find the injected risk scenarios.

    python -m scripts.generate_synthetic_data
    python -m evaluation.evaluate_rules

Prints the results and writes docs/evaluation.md.

The data is synthetic, so these numbers show how the rules trade alert
volume for coverage on a known population. They are not a claim about
detection rates on real ledgers.
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

from app.config import DEFAULT_WEIGHTS, load_config
from app.engine.risk_engine import EntryRiskResult, RiskLevel
from app.services.analysis import analyze_files

K_VALUES = (50, 100, 200, 500)


def load_ground_truth(path: Path) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as file:
        return {row["entry_id"]: row["scenario"] for row in csv.DictReader(file)}


def percent(part: int, whole: int, digits: int = 1) -> str:
    return f"{100 * part / whole:.{digits}f}%" if whole else "-"


def scenario_table(results: list[EntryRiskResult], truth: dict[str, str]) -> list[list[str]]:
    """For each injected scenario: how many entries ended up HIGH, and MEDIUM or HIGH."""
    total = Counter()
    high = Counter()
    medium_or_high = Counter()
    for result in results:
        scenario = truth.get(result.entry_id)
        if scenario is None:
            continue
        total[scenario] += 1
        if result.risk_level == RiskLevel.HIGH:
            high[scenario] += 1
        if result.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM):
            medium_or_high[scenario] += 1
    rows = []
    for scenario in sorted(total):
        rows.append([scenario, str(total[scenario]), percent(high[scenario], total[scenario]),
                     percent(medium_or_high[scenario], total[scenario])])
    all_injected = sum(total.values())
    rows.append(["**All injected**", str(all_injected), percent(sum(high.values()), all_injected),
                 percent(sum(medium_or_high.values()), all_injected)])
    return rows


def alert_table(results: list[EntryRiskResult], truth: dict[str, str]) -> list[list[str]]:
    """How many alerts each level produces, and how many of them are real (injected)."""
    normal_count = sum(1 for result in results if result.entry_id not in truth)
    rows = []
    for name, levels in (("HIGH", {RiskLevel.HIGH}), ("MEDIUM or HIGH", {RiskLevel.HIGH, RiskLevel.MEDIUM})):
        flagged = [result for result in results if result.risk_level in levels]
        injected = sum(1 for result in flagged if result.entry_id in truth)
        false_alarms = len(flagged) - injected
        rows.append([name, str(len(flagged)), str(injected), percent(injected, len(flagged)),
                     str(false_alarms), percent(false_alarms, normal_count, digits=3)])
    return rows


def top_k_table(results: list[EntryRiskResult], truth: dict[str, str]) -> list[list[str]]:
    """If the audit team can only review the top K entries, how many of them are real?"""
    rows = []
    for k in K_VALUES:
        found = sum(1 for result in results[:k] if result.entry_id in truth)
        rows.append([str(k), str(found), percent(found, k), percent(found, len(truth))])
    return rows


def rule_table(results: list[EntryRiskResult], truth: dict[str, str]) -> list[list[str]]:
    """How often each rule fires on injected entries versus normal manual entries."""
    injected_hits = Counter()
    normal_manual_hits = Counter()
    normal_manual_count = 0
    for result in results:
        codes = {hit.rule_code for hit in result.hits}
        if result.entry_id in truth:
            injected_hits.update(codes)
        elif result.entry.source == "MANUAL":
            normal_manual_count += 1
            normal_manual_hits.update(codes)
    rows = []
    for code in DEFAULT_WEIGHTS:
        rows.append([code, percent(injected_hits[code], len(truth)),
                     percent(normal_manual_hits[code], normal_manual_count), str(normal_manual_hits[code])])
    return rows


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the risk rules on the synthetic ledger.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--out", type=Path, default=Path("docs/evaluation.md"))
    args = parser.parse_args()

    analysis = analyze_files(
        args.data / "gl_detail.csv", args.data / "trial_balance.csv", load_config(args.data / "client_config.json")
    )
    truth = load_ground_truth(args.data / "ground_truth.csv")
    results = analysis.results

    sections = [
        "# Rule Evaluation",
        "",
        f"Synthetic ledger: {len(results):,} entries, {len(truth)} injected risk scenarios "
        f"({100 * len(truth) / len(results):.1f}%). Generated with "
        "`python -m scripts.generate_synthetic_data` (default seed 42); "
        "produced by `python -m evaluation.evaluate_rules`.",
        "",
        "Synthetic data shows how the rules behave on a known population. "
        "It is not evidence of detection rates on real ledgers.",
        "",
        "## Coverage by scenario",
        "",
        markdown_table(["Scenario", "Entries", "Ranked HIGH", "Ranked MEDIUM or HIGH"],
                       scenario_table(results, truth)),
        "",
        "## Alert volume",
        "",
        markdown_table(["Level", "Alerts", "Injected", "Precision", "False alarms", "False alarm rate"],
                       alert_table(results, truth)),
        "",
        "False alarm rate = false alarms / all normal entries.",
        "",
        "## If the team can only review the top K",
        "",
        markdown_table(["K", "Injected in top K", "Precision@K", "Recall@K"], top_k_table(results, truth)),
        "",
        "## Rule firing rates",
        "",
        markdown_table(["Rule", "Fires on injected", "Fires on normal manual", "Normal manual hits"],
                       rule_table(results, truth)),
        "",
    ]
    report = "\n".join(sections)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
