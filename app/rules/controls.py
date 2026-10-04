"""Rules about approval controls."""

from app.domain.journal_entry import JournalEntry
from app.rules.base import RiskRule, RuleContext, RuleHit


class MissingApprovalRule(RiskRule):
    code = "MISSING_APPROVAL"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        minimum = context.config.missing_approval_min
        if entry.approved_by is None and entry.amount > minimum:
            return self.hit(
                context, f"Amount {entry.amount:,.2f} exceeds {minimum:,.2f} and the entry has no approver."
            )
        return None


class SelfApprovalRule(RiskRule):
    """The preparer also approved the entry: no segregation of duties."""

    code = "SELF_APPROVAL"
    manual_only = False

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        if entry.approved_by is None:
            return None  # MISSING_APPROVAL covers entries without an approver
        # User IDs can differ only in case or spacing: "gl_acct1" and "GL_ACCT1" are the same person.
        if entry.prepared_by.strip().lower() == entry.approved_by.strip().lower():
            return self.hit(context, f"Entry was prepared and approved by the same user ({entry.prepared_by}).")
        return None
