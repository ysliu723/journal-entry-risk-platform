"""Rules that compare an entry with the whole population."""

from app.domain.journal_entry import JournalEntry
from app.rules.base import RiskRule, RuleContext, RuleHit


class SeldomUsedAccountRule(RiskRule):
    code = "SELDOM_USED_ACCOUNT"
    manual_only = False

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        stats = context.stats
        minimum = context.config.min_account_usage
        rare_accounts = []
        for account_number in sorted(entry.account_numbers):
            usage = stats.account_usage.get(account_number, 0)
            if usage < minimum:
                rare_accounts.append(f"{account_number} (used by {usage} of {stats.entry_count} entries)")
        if rare_accounts:
            return self.hit(context, "Uses seldom-used account(s): " + ", ".join(rare_accounts) + ".")
        return None


class InfrequentPreparerRule(RiskRule):
    """Prepared by someone who rarely posts entries, e.g. a senior manager."""

    code = "INFREQUENT_PREPARER"
    manual_only = False

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        stats = context.stats
        count = stats.preparer_entries.get(entry.prepared_by, 0)
        if count < context.config.min_preparer_entries:
            return self.hit(
                context, f"Preparer {entry.prepared_by} prepared only {count} of {stats.entry_count} entries."
            )
        return None
