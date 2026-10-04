"""The risk rule interface, and what every rule receives."""

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass

from app.config import ClientConfig
from app.domain.journal_entry import JournalEntry


@dataclass(frozen=True)
class RuleHit:
    rule_code: str
    rule_version: int
    score: int
    reason: str


@dataclass(frozen=True)
class PopulationStats:
    """Statistics over the whole population, computed once per run.

    Population rules ("seldom-used account", "infrequent preparer") cannot be
    decided from one entry alone; they compare the entry with all the others.
    """

    entry_count: int
    account_usage: dict[str, int]  # account number -> number of entries that use it
    preparer_entries: dict[str, int]  # preparer -> number of entries they prepared

    @classmethod
    def from_entries(cls, entries: list[JournalEntry]) -> "PopulationStats":
        account_usage = Counter()
        preparer_entries = Counter()
        for entry in entries:
            for account_number in entry.account_numbers:  # each account once per entry
                account_usage[account_number] += 1
            preparer_entries[entry.prepared_by] += 1
        return cls(len(entries), dict(account_usage), dict(preparer_entries))


@dataclass(frozen=True)
class RuleContext:
    config: ClientConfig
    stats: PopulationStats


class RiskRule(ABC):
    code: str
    version: int = 1
    # Most rules only make sense for entries keyed in by people. System batch
    # jobs post at night, on weekends, at period end, and without a named
    # approver by design; flagging them would bury auditors in false positives.
    manual_only: bool = True

    @abstractmethod
    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        """Return a RuleHit if the rule is triggered, otherwise None."""

    def hit(self, context: RuleContext, reason: str) -> RuleHit:
        return RuleHit(self.code, self.version, context.config.weight(self.code), reason)
