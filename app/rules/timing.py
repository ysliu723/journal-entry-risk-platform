"""Rules about when an entry was posted or keyed in."""

from app.domain.journal_entry import JournalEntry
from app.rules.base import RiskRule, RuleContext, RuleHit


class WeekendEntryRule(RiskRule):
    code = "WEEKEND_ENTRY"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        if entry.created_at.weekday() >= 5:  # Monday is 0, Saturday 5, Sunday 6
            return self.hit(context, f"Entry was created on a {entry.created_at:%A}.")
        return None


class LateNightEntryRule(RiskRule):
    code = "LATE_NIGHT_ENTRY"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        config = context.config
        hour = entry.created_at.hour
        if hour >= config.late_night_start_hour or hour < config.late_night_end_hour:
            return self.hit(context, f"Entry was created at {entry.created_at:%H:%M}.")
        return None


class PeriodEndEntryRule(RiskRule):
    code = "PERIOD_END_ENTRY"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        config = context.config
        days_before_year_end = (config.fiscal_year_end - entry.posting_date).days
        if 0 <= days_before_year_end < config.period_end_window_days:
            return self.hit(
                context,
                f"Entry was posted on {entry.posting_date}, within {config.period_end_window_days} days "
                f"of fiscal year end {config.fiscal_year_end}.",
            )
        return None


class PostCloseEntryRule(RiskRule):
    """Keyed in after the books were closed, but dated inside the closed year."""

    code = "POST_CLOSE_ENTRY"
    manual_only = False  # a system posting into a closed year is just as unusual

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        config = context.config
        created_on = entry.created_at.date()
        if entry.posting_date <= config.fiscal_year_end and created_on > config.period_close_date:
            return self.hit(
                context,
                f"Entry was created on {created_on}, after the books closed on {config.period_close_date}, "
                f"but posted to {entry.posting_date}.",
            )
        return None
