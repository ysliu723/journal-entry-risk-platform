"""All risk rules. default_rules() is the rule set used for a normal run."""

from app.rules.amount import NearApprovalThresholdRule, RoundAmountRule
from app.rules.base import RiskRule
from app.rules.controls import MissingApprovalRule, SelfApprovalRule
from app.rules.description import VagueDescriptionRule
from app.rules.population import InfrequentPreparerRule, SeldomUsedAccountRule
from app.rules.timing import LateNightEntryRule, PeriodEndEntryRule, PostCloseEntryRule, WeekendEntryRule


def default_rules() -> list[RiskRule]:
    return [
        WeekendEntryRule(),
        LateNightEntryRule(),
        PeriodEndEntryRule(),
        PostCloseEntryRule(),
        RoundAmountRule(),
        NearApprovalThresholdRule(),
        MissingApprovalRule(),
        SelfApprovalRule(),
        VagueDescriptionRule(),
        SeldomUsedAccountRule(),
        InfrequentPreparerRule(),
    ]
