import pytest

from app.domain.journal_entry import EntrySource
from app.engine.risk_engine import RiskEngine, RiskLevel
from app.rules.base import PopulationStats, RiskRule, RuleHit
from tests.factories import entry_with_amount, line, make_config, make_entry


class FixedRule(RiskRule):
    """Test double: always fires with a fixed score."""

    def __init__(self, code, score, manual_only=True):
        self.code = code
        self.score = score
        self.manual_only = manual_only

    def evaluate(self, entry, context):
        return RuleHit(self.code, self.version, self.score, f"{self.code} fired")


class NeverFires(RiskRule):
    code = "NEVER"

    def evaluate(self, entry, context):
        return None


def score_one(rules, entry):
    [result] = RiskEngine(rules, make_config()).score_population([entry])
    return result


def test_scores_are_added_up_and_hits_sorted_by_score():
    result = score_one([FixedRule("A", 10), FixedRule("B", 30), NeverFires()], make_entry())
    assert result.risk_score == 40
    assert [hit.rule_code for hit in result.hits] == ["B", "A"]


@pytest.mark.parametrize(
    "score, level",
    [(0, RiskLevel.LOW), (29, RiskLevel.LOW), (30, RiskLevel.MEDIUM), (59, RiskLevel.MEDIUM), (60, RiskLevel.HIGH)],
)
def test_risk_level_boundaries(score, level):
    assert RiskEngine([], make_config()).risk_level(score) == level


def test_manual_only_rules_skip_system_entries():
    rules = [FixedRule("MANUAL_ONLY", 10), FixedRule("ANY_SOURCE", 5, manual_only=False)]

    manual = score_one(rules, make_entry(source=EntrySource.MANUAL))
    system = score_one(rules, make_entry(source=EntrySource.SYSTEM))

    assert [hit.rule_code for hit in manual.hits] == ["MANUAL_ONLY", "ANY_SOURCE"]
    assert [hit.rule_code for hit in system.hits] == ["ANY_SOURCE"]


def test_population_is_ranked_highest_first_with_ties_broken_by_entry_id():
    class AmountRule(RiskRule):
        code = "AMOUNT"

        def evaluate(self, entry, context):
            return RuleHit(self.code, self.version, int(entry.amount), "amount")

    entries = [
        entry_with_amount("10", entry_id="JE000003"),
        entry_with_amount("50", entry_id="JE000002"),
        entry_with_amount("10", entry_id="JE000001"),
    ]
    results = RiskEngine([AmountRule()], make_config()).score_population(entries)
    assert [(r.entry_id, r.risk_score) for r in results] == [("JE000002", 50), ("JE000001", 10), ("JE000003", 10)]


def test_rejects_the_same_rule_twice():
    with pytest.raises(ValueError, match="only appear once"):
        RiskEngine([FixedRule("A", 10), FixedRule("A", 20)], make_config())


def test_rule_set_version_lists_every_rule():
    engine = RiskEngine([FixedRule("B", 1), FixedRule("A", 1)], make_config())
    assert engine.rule_set_version == "A@1,B@1"


def test_population_stats_count_each_account_once_per_entry():
    entries = [
        make_entry(line(1, "610100", debit="5"), line(2, "610100", debit="5"), line(3, "200100", credit="10")),
        entry_with_amount("1", prepared_by="ops_mgr"),
    ]
    stats = PopulationStats.from_entries(entries)
    assert stats.entry_count == 2
    assert stats.account_usage == {"610100": 2, "200100": 2}
    assert stats.preparer_entries == {"gl_acct1": 1, "ops_mgr": 1}
