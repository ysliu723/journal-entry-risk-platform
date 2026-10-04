"""A whole risk run without a database: load, check, score.

The command line and the API both call analyze(), so they always do
exactly the same thing.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from app.config import ClientConfig
from app.domain.trial_balance import TrialBalanceLine
from app.engine.risk_engine import EntryRiskResult, RiskEngine
from app.ingestion.csv_loader import LoadResult, read_gl_detail, read_trial_balance
from app.integrity.checks import IntegrityFinding, run_all_checks
from app.rules import default_rules


@dataclass
class RunAnalysis:
    config: ClientConfig
    load: LoadResult
    trial_balance: list[TrialBalanceLine]
    findings: list[IntegrityFinding]
    results: list[EntryRiskResult]  # ranked, highest risk first
    rule_set_version: str

    @property
    def integrity_passed(self) -> bool:
        return not self.findings and not self.load.errors


def analyze(gl_file: TextIO, trial_balance_file: TextIO, config: ClientConfig) -> RunAnalysis:
    load = read_gl_detail(gl_file)
    trial_balance = read_trial_balance(trial_balance_file)
    findings = run_all_checks(load.entries, trial_balance)
    engine = RiskEngine(default_rules(), config)
    results = engine.score_population(load.entries)
    return RunAnalysis(config, load, trial_balance, findings, results, engine.rule_set_version)


def analyze_files(gl_path: str | Path, trial_balance_path: str | Path, config: ClientConfig) -> RunAnalysis:
    with open(gl_path, newline="", encoding="utf-8") as gl_file:
        with open(trial_balance_path, newline="", encoding="utf-8") as trial_balance_file:
            return analyze(gl_file, trial_balance_file, config)
