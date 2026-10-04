"""Rules about the entry description."""

import re

from app.domain.journal_entry import JournalEntry
from app.rules.base import RiskRule, RuleContext, RuleHit


class VagueDescriptionRule(RiskRule):
    """No description, or one that explains nothing ("adjustment", "per CFO", "misc")."""

    code = "VAGUE_DESCRIPTION"

    def evaluate(self, entry: JournalEntry, context: RuleContext) -> RuleHit | None:
        description = entry.description.strip()
        if not description:
            return self.hit(context, "Entry has no description.")
        lowered = description.lower()
        for keyword in context.config.vague_description_keywords:
            # \b means "start of a word": "other" matches "Other expenses" but not "Mother's Day".
            if re.search(r"\b" + re.escape(keyword), lowered):
                return self.hit(context, f"Description '{description}' is generic (contains '{keyword}').")
        return None
