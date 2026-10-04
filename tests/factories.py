"""Small builders so each test only spells out the fields it cares about."""

from datetime import date, datetime
from decimal import Decimal

from app.domain.journal_entry import EntrySource, JournalEntry, JournalEntryLine


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
