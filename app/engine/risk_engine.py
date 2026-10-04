"""Score every entry against the rules, then rank the population."""

from dataclasses import dataclass
from enum import StrEnum

from app.config import ClientConfig
from app.domain.journal_entry import EntrySource, JournalEntry
from app.rules.base import PopulationStats, RiskRule, RuleContext, RuleHit


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class EntryRiskResult:
    entry: JournalEntry
    risk_score: int
    risk_level: RiskLevel
    hits: tuple[RuleHit, ...]

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id


class RiskEngine:
    def __init__(self, rules: list[RiskRule], config: ClientConfig):
        codes = [rule.code for rule in rules]
        if len(codes) != len(set(codes)):
            raise ValueError("each rule code may only appear once")
        self.rules = list(rules)
        self.config = config

    @property
    def rule_set_version(self) -> str:
        """Which rules and versions produce the scores, e.g. 'LATE_NIGHT_ENTRY@1,WEEKEND_ENTRY@1'."""
        return ",".join(sorted(f"{rule.code}@{rule.version}" for rule in self.rules))

    def score_population(self, entries: list[JournalEntry]) -> list[EntryRiskResult]:
        """Score every entry and return the results ranked highest risk first."""
        context = RuleContext(self.config, PopulationStats.from_entries(entries))
        results = [self.score_entry(entry, context) for entry in entries]
        # Ties are broken by entry ID so the ranking never changes between runs.
        results.sort(key=lambda result: (-result.risk_score, result.entry_id))
        return results

    def score_entry(self, entry: JournalEntry, context: RuleContext) -> EntryRiskResult:
        hits = []
        for rule in self.rules:
            if rule.manual_only and entry.source != EntrySource.MANUAL:
                continue
            hit = rule.evaluate(entry, context)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda hit: (-hit.score, hit.rule_code))
        score = sum(hit.score for hit in hits)
        return EntryRiskResult(entry, score, self.risk_level(score), tuple(hits))

    def risk_level(self, score: int) -> RiskLevel:
        if score >= self.config.high_threshold:
            return RiskLevel.HIGH
        if score >= self.config.medium_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
