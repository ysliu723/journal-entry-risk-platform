from decimal import Decimal

import pytest

from app.domain.journal_entry import JournalEntryLine
from tests.factories import line, make_entry


class TestJournalEntryLine:
    def test_rejects_float_amounts(self):
        with pytest.raises(TypeError, match="Decimal"):
            JournalEntryLine(1, "610100", 0.1, Decimal("0"))

    def test_rejects_negative_amounts(self):
        with pytest.raises(ValueError, match="negative"):
            line(1, "610100", debit="-5.00")

    def test_rejects_debit_and_credit_on_same_line(self):
        with pytest.raises(ValueError, match="both"):
            line(1, "610100", debit="5.00", credit="5.00")

    def test_rejects_line_without_amount(self):
        with pytest.raises(ValueError, match="either"):
            line(1, "610100")


class TestJournalEntry:
    def test_totals_for_multi_line_entry(self):
        entry = make_entry(
            line(1, "610100", debit="60000.00"),
            line(2, "610200", debit="39800.00"),
            line(3, "200100", credit="99800.00"),
        )
        assert entry.total_debit == Decimal("99800.00")
        assert entry.total_credit == Decimal("99800.00")
        assert entry.amount == Decimal("99800.00")
        assert entry.is_balanced
        assert entry.account_numbers == {"610100", "610200", "200100"}

    def test_unbalanced_entry_is_allowed_but_reported_as_unbalanced(self):
        entry = make_entry(line(1, "610100", debit="1000.00"), line(2, "200100", credit="900.00"))
        assert not entry.is_balanced

    def test_decimal_cents_add_up_exactly(self):
        # With floats, 0.1 + 0.2 == 0.30000000000000004 and this entry would look unbalanced.
        assert 0.1 + 0.2 != 0.3
        entry = make_entry(
            line(1, "610100", debit="0.10"),
            line(2, "610100", debit="0.20"),
            line(3, "200100", credit="0.30"),
        )
        assert entry.is_balanced

    def test_rejects_entry_without_lines(self):
        with pytest.raises(ValueError, match="no lines"):
            make_entry(lines=())

    def test_rejects_duplicate_line_numbers(self):
        with pytest.raises(ValueError, match="duplicate line numbers"):
            make_entry(line(1, "610100", debit="5.00"), line(1, "200100", credit="5.00"))

    def test_lines_are_stored_as_tuple(self):
        entry = make_entry(lines=[line(1, "610100", debit="5.00"), line(2, "200100", credit="5.00")])
        assert isinstance(entry.lines, tuple)

    def test_entry_is_immutable(self):
        entry = make_entry()
        with pytest.raises(AttributeError):
            entry.approved_by = "someone_else"
