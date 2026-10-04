"""Client-specific settings for a risk run.

Every client has its own fiscal year end, close date, and approval
matrix, so none of these values are hard-coded in the rules.
"""

import json
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

# Score added when a rule is triggered. Clients can override any of them.
DEFAULT_WEIGHTS = {
    "WEEKEND_ENTRY": 10,
    "LATE_NIGHT_ENTRY": 20,
    "ROUND_AMOUNT": 10,
    "NEAR_APPROVAL_THRESHOLD": 30,
    "MISSING_APPROVAL": 40,
    "SELF_APPROVAL": 40,
    "PERIOD_END_ENTRY": 20,
    "POST_CLOSE_ENTRY": 30,
    "VAGUE_DESCRIPTION": 10,
    "SELDOM_USED_ACCOUNT": 20,
    "INFREQUENT_PREPARER": 20,
}

# Words that often mark an entry without a real business explanation.
DEFAULT_VAGUE_KEYWORDS = (
    "adjust",
    "misc",
    "other",
    "plug",
    "reclass",
    "per cfo",
    "per ceo",
    "per controller",
    "to fix",
)

_DATE_FIELDS = ("fiscal_year_end", "period_close_date")
_DECIMAL_FIELDS = (
    "approval_threshold",
    "near_threshold_ratio",
    "missing_approval_min",
    "round_amount_min",
    "round_amount_unit",
)


@dataclass(frozen=True)
class ClientConfig:
    fiscal_year_end: date
    period_close_date: date  # the books for the fiscal year were closed on this date

    # Amount rules
    approval_threshold: Decimal = Decimal("100000")
    near_threshold_ratio: Decimal = Decimal("0.95")
    missing_approval_min: Decimal = Decimal("50000")
    round_amount_min: Decimal = Decimal("10000")
    round_amount_unit: Decimal = Decimal("1000")

    # Timing rules: created at or after late_night_start_hour, or before late_night_end_hour
    late_night_start_hour: int = 22
    late_night_end_hour: int = 6
    period_end_window_days: int = 3

    # Description and population rules
    vague_description_keywords: tuple[str, ...] = DEFAULT_VAGUE_KEYWORDS
    min_account_usage: int = 5
    min_preparer_entries: int = 5

    # Scoring
    weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    medium_threshold: int = 30
    high_threshold: int = 60

    def weight(self, rule_code: str) -> int:
        return self.weights[rule_code]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ClientConfig":
        """Build a config from JSON-style values (dates and amounts as strings)."""
        values = dict(raw)
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown config keys: {', '.join(unknown)}")
        for name in _DATE_FIELDS:
            if name not in values:
                raise ValueError(f"config is missing {name}")
            values[name] = date.fromisoformat(values[name])
        for name in _DECIMAL_FIELDS:
            if name in values:
                # str() first, so a JSON number like 0.95 becomes exactly Decimal("0.95")
                values[name] = Decimal(str(values[name]))
        if "vague_description_keywords" in values:
            values["vague_description_keywords"] = tuple(k.lower() for k in values["vague_description_keywords"])
        if "weights" in values:
            unknown_rules = sorted(set(values["weights"]) - set(DEFAULT_WEIGHTS))
            if unknown_rules:
                raise ValueError(f"unknown rule codes in weights: {', '.join(unknown_rules)}")
            values["weights"] = {**DEFAULT_WEIGHTS, **values["weights"]}
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        """The opposite of from_dict, for storing the config with a run."""
        result = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, (date, Decimal)):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            elif isinstance(value, dict):
                value = dict(value)
            result[f.name] = value
        return result


def load_config(path: str | Path) -> ClientConfig:
    return ClientConfig.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
