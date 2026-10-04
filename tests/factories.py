"""Small builders so each test only spells out the fields it cares about."""

from datetime import date, datetime
from decimal import Decimal

from app.config import ClientConfig
from app.domain.journal_entry import EntrySource, JournalEntry, JournalEntryLine
from app.domain.trial_balance import AccountType, TrialBalanceLine
from app.rules.base import PopulationStats, RuleContext


def line(line_number, account_number, debit="0", credit="0", **kwargs) -> JournalEntryLine:
    return JournalEntryLine(line_number, account_number, Decimal(debit), Decimal(credit), **kwargs)


def make_entry(*lines, **overrides) -> JournalEntry:
    """A balanced, unremarkable manual entry: Wednesday 10:00, approved, 1,250.00."""
    fields = {
        "entry_id": "JE000001",
        "posting_date": date(2026, 11, 18),
        "created_at": datetime(2026, 11, 18, 10, 0),
        "source": EntrySource.MANUAL,
        "description": "Consulting fee - Fabrikam project",
        "prepared_by": "gl_acct1",
        "approved_by": "gl_manager",
        "currency": "USD",
        "lines": lines or (line(1, "610100", debit="1250.00"), line(2, "200100", credit="1250.00")),
    }
    fields.update(overrides)
    return JournalEntry(**fields)


def entry_with_amount(amount, **overrides) -> JournalEntry:
    """A two-line entry: debit 610100 (expense), credit 200100 (payables)."""
    return make_entry(line(1, "610100", debit=amount), line(2, "200100", credit=amount), **overrides)


def tb_line(account_number, opening, closing, account_type=AccountType.EXPENSE) -> TrialBalanceLine:
    return TrialBalanceLine(
        account_number, f"Account {account_number}", account_type, Decimal(opening), Decimal(closing)
    )


def make_config(**overrides) -> ClientConfig:
    """Calendar fiscal year 2026, books closed on 2027-01-10, default thresholds."""
    fields = {"fiscal_year_end": date(2026, 12, 31), "period_close_date": date(2027, 1, 10)}
    fields.update(overrides)
    return ClientConfig(**fields)


def make_context(entries=(), config=None) -> RuleContext:
    """What a rule receives: the config plus statistics over `entries`."""
    return RuleContext(config or make_config(), PopulationStats.from_entries(list(entries)))
