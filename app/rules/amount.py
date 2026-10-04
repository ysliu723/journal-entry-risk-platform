"""Rules about the size of an entry."""

from app.domain.journal_entry import JournalEntry
from app.rules.base import RiskRule, RuleContext, RuleHit


class RoundAmountRule(RiskRule):
    """Real invoices rarely come to exactly 30,000.00; estimates and made-up numbers often do."""

    code = "ROUND_AMOUNT"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        config = context.config
        amount = entry.amount
        if amount >= config.round_amount_min and amount % config.round_amount_unit == 0:
            return self.hit(context, f"Amount {amount:,.2f} is a round multiple of {config.round_amount_unit:,}.")
        return None


class NearApprovalThresholdRule(RiskRule):
    """Just below the amount that needs extra approval: a classic way to avoid a control."""

    code = "NEAR_APPROVAL_THRESHOLD"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        config = context.config
        threshold = config.approval_threshold
        lower_bound = threshold * config.near_threshold_ratio
        if lower_bound <= entry.amount < threshold:
            percent = (1 - config.near_threshold_ratio) * 100
            return self.hit(
                context,
                f"Amount {entry.amount:,.2f} is within {percent:.0f}% below the "
                f"{threshold:,.2f} approval threshold.",
            )
        return None
