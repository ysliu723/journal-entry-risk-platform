import csv
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.journal_entry import EntrySource
from app.ingestion.csv_loader import GL_DETAIL_COLUMNS, load_gl_detail

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def gl_row(**overrides) -> dict:
    row = {
        "entry_id": "JE000001",
        "line_number": "1",
        "posting_date": "2026-11-18",
        "created_at": "2026-11-18 10:00:00",
        "source": "MANUAL",
        "description": "Consulting fee - Fabrikam project",
        "prepared_by": "gl_acct1",
        "approved_by": "gl_manager",
        "currency": "USD",
        "account_number": "610100",
        "debit": "1250.00",
        "credit": "",
        "department": "",
        "vendor": "",
    }
    row.update(overrides)
    return row


def balanced_rows(entry_id="JE000001", amount="1250.00", **overrides) -> list[dict]:
    return [
        gl_row(entry_id=entry_id, line_number="1", account_number="610100", debit=amount, credit="", **overrides),
        gl_row(entry_id=entry_id, line_number="2", account_number="200100", debit="", credit=amount, **overrides),
    ]


def write_csv(path: Path, rows: list[dict], columns=GL_DETAIL_COLUMNS) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_loads_sample_file():
    result = load_gl_detail(SAMPLE_DIR / "gl_detail.csv")
    assert result.errors == []
    assert len(result.entries) == 14
    assert all(entry.is_balanced for entry in result.entries)
    payroll = result.entries[1]
    assert payroll.entry_id == "JE000002"
    assert len(payroll.lines) == 3


def test_groups_rows_into_header_and_lines(tmp_path):
    path = write_csv(tmp_path / "gl.csv", balanced_rows(approved_by=""))
    [entry] = load_gl_detail(path).entries
    assert entry.entry_id == "JE000001"
    assert entry.source is EntrySource.MANUAL
    assert entry.approved_by is None
    assert [line.line_number for line in entry.lines] == [1, 2]


def test_amounts_are_parsed_from_text_as_exact_decimals(tmp_path):
    path = write_csv(tmp_path / "gl.csv", balanced_rows(amount="1250.10"))
    [entry] = load_gl_detail(path).entries
    assert entry.lines[0].debit == Decimal("1250.10")
    assert entry.lines[0].credit == Decimal("0")


def test_invalid_rows_are_reported_and_their_entry_left_out(tmp_path):
    rows = [
        *balanced_rows("JE000001"),
        gl_row(entry_id="JE000002", line_number="1", posting_date="2026-13-45"),
        gl_row(entry_id="JE000002", line_number="2", account_number="200100", debit="", credit="-1250.00"),
        *balanced_rows("JE000003"),
    ]
    result = load_gl_detail(write_csv(tmp_path / "gl.csv", rows))

    # Both bad rows are reported, not just the first one.
    assert [(error.row_number, error.entry_id) for error in result.errors] == [(4, "JE000002"), (5, "JE000002")]
    assert "posting_date" in result.errors[0].message
    assert "negative" in result.errors[1].message
    # JE000002 is left out as a whole; the others still load.
    assert [entry.entry_id for entry in result.entries] == ["JE000001", "JE000003"]


@pytest.mark.parametrize(
    "column, value, message",
    [
        ("debit", "1,250.00", "not a valid amount"),
        ("debit", "NaN", "not a valid amount"),
        ("created_at", "yesterday", "created_at"),
        ("source", "EMAIL", "source must be one of MANUAL, SYSTEM, RECURRING"),
        ("prepared_by", "  ", "prepared_by is blank"),
        ("line_number", "one", "line_number"),
    ],
)
def test_rejects_malformed_values(tmp_path, column, value, message):
    rows = balanced_rows()
    rows[0][column] = value
    result = load_gl_detail(write_csv(tmp_path / "gl.csv", rows))
    assert result.entries == []
    assert message in result.errors[0].message


def test_repeated_line_numbers_become_a_second_entry_with_the_same_id(tmp_path):
    # The same entry exported twice: lines 1, 2, 1, 2.
    rows = balanced_rows("JE000001") + balanced_rows("JE000001")
    result = load_gl_detail(write_csv(tmp_path / "gl.csv", rows))
    assert result.errors == []
    assert [entry.entry_id for entry in result.entries] == ["JE000001", "JE000001"]


def test_different_header_under_the_same_id_becomes_a_second_entry(tmp_path):
    rows = balanced_rows("JE000001") + balanced_rows("JE000001", prepared_by="someone_else")
    result = load_gl_detail(write_csv(tmp_path / "gl.csv", rows))
    assert {entry.prepared_by for entry in result.entries} == {"gl_acct1", "someone_else"}


def test_missing_column_fails_the_whole_file(tmp_path):
    columns = [name for name in GL_DETAIL_COLUMNS if name != "created_at"]
    rows = [{k: v for k, v in row.items() if k != "created_at"} for row in balanced_rows()]
    path = write_csv(tmp_path / "gl.csv", rows, columns)
    with pytest.raises(ValueError, match="missing columns: created_at"):
        load_gl_detail(path)
