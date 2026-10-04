"""Load a flat GL detail export and a trial balance from CSV.

A GL detail export has one row per journal entry line, with the header
fields (dates, preparer, description, ...) repeated on every row. That is
how most ERP systems export journal entries.

Invalid GL rows are collected as LoadErrors instead of stopping at the
first one, so one run reports every problem in the file. An entry with any
invalid row is left out as a whole, never loaded half-complete.
"""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from app.domain.journal_entry import EntrySource, JournalEntry, JournalEntryLine
from app.domain.trial_balance import AccountType, TrialBalanceLine

TRIAL_BALANCE_COLUMNS = ("account_number", "account_name", "account_type", "opening_balance", "closing_balance")

GL_DETAIL_COLUMNS = (
    "entry_id",
    "line_number",
    "posting_date",
    "created_at",
    "source",
    "description",
    "prepared_by",
    "approved_by",
    "currency",
    "account_number",
    "debit",
    "credit",
    "department",
    "vendor",
)


@dataclass(frozen=True)
class LoadError:
    row_number: int  # physical line in the file; the column header is line 1
    entry_id: str
    message: str


@dataclass
class LoadResult:
    entries: list[JournalEntry] = field(default_factory=list)
    errors: list[LoadError] = field(default_factory=list)


@dataclass(frozen=True)
class _EntryHeader:
    """The header fields that are repeated on every row of one entry."""

    posting_date: date
    created_at: datetime
    source: EntrySource
    description: str
    prepared_by: str
    approved_by: str | None
    currency: str


@dataclass
class _EntryGroup:
    """The rows of one journal entry, collected while the file is read."""

    header: _EntryHeader
    lines: list[JournalEntryLine] = field(default_factory=list)

    def accepts(self, header: _EntryHeader, line_number: int) -> bool:
        """A row belongs to this group if its header matches and its line number is new."""
        if header != self.header:
            return False
        for line in self.lines:
            if line.line_number == line_number:
                return False
        return True

    def to_entry(self, entry_id: str) -> JournalEntry:
        return JournalEntry(
            entry_id=entry_id,
            posting_date=self.header.posting_date,
            created_at=self.header.created_at,
            source=self.header.source,
            description=self.header.description,
            prepared_by=self.header.prepared_by,
            approved_by=self.header.approved_by,
            currency=self.header.currency,
            lines=tuple(self.lines),
        )


def load_gl_detail(path: str | Path) -> LoadResult:
    with open(path, newline="", encoding="utf-8") as file:
        return read_gl_detail(file)


def read_gl_detail(file: TextIO) -> LoadResult:
    """Read a GL detail export from an open text file, such as an uploaded file."""
    result = LoadResult()
    groups_by_id: dict[str, list[_EntryGroup]] = {}
    rejected_ids: set[str] = set()

    reader = csv.DictReader(file, restval="")
    _require_columns(reader.fieldnames, GL_DETAIL_COLUMNS)
    for row in reader:
        entry_id = row["entry_id"].strip()
        try:
            header, line = _parse_gl_row(row)
        except ValueError as exc:
            result.errors.append(LoadError(reader.line_num, entry_id, str(exc)))
            rejected_ids.add(entry_id)
            continue

        # Rows sharing an entry_id normally form one entry. A repeated line
        # number or a different header means a second entry reusing the ID;
        # keep it separate so the integrity checks can report it.
        if entry_id not in groups_by_id:
            groups_by_id[entry_id] = []
        groups = groups_by_id[entry_id]
        group = _find_group(groups, header, line.line_number)
        if group is None:
            group = _EntryGroup(header)
            groups.append(group)
        group.lines.append(line)

    for entry_id, groups in groups_by_id.items():
        if entry_id in rejected_ids:
            continue
        for group in groups:
            result.entries.append(group.to_entry(entry_id))
    return result


def _find_group(groups: list[_EntryGroup], header: _EntryHeader, line_number: int) -> _EntryGroup | None:
    for group in groups:
        if group.accepts(header, line_number):
            return group
    return None


def load_trial_balance(path: str | Path) -> list[TrialBalanceLine]:
    with open(path, newline="", encoding="utf-8") as file:
        return read_trial_balance(file)


def read_trial_balance(file: TextIO) -> list[TrialBalanceLine]:
    """Read a trial balance from an open text file.

    Unlike the GL detail, any invalid row fails the whole file: the trial
    balance is what the GL is reconciled to, so a partial one is useless.
    """
    lines = []
    seen_accounts = set()
    reader = csv.DictReader(file, restval="")
    _require_columns(reader.fieldnames, TRIAL_BALANCE_COLUMNS)
    for row in reader:
        try:
            tb_line = TrialBalanceLine(
                account_number=_required(row["account_number"], "account_number"),
                account_name=_required(row["account_name"], "account_name"),
                account_type=_parse_enum(AccountType, row["account_type"], "account_type"),
                opening_balance=_parse_amount(row["opening_balance"], "opening_balance"),
                closing_balance=_parse_amount(row["closing_balance"], "closing_balance"),
            )
        except ValueError as exc:
            raise ValueError(f"trial balance line {reader.line_num}: {exc}") from None
        if tb_line.account_number in seen_accounts:
            raise ValueError(
                f"trial balance line {reader.line_num}: account {tb_line.account_number} appears twice"
            )
        seen_accounts.add(tb_line.account_number)
        lines.append(tb_line)
    return lines


def _parse_gl_row(row: dict[str, str]) -> tuple[_EntryHeader, JournalEntryLine]:
    _required(row["entry_id"], "entry_id")
    header = _EntryHeader(
        posting_date=_parse_date(row["posting_date"], "posting_date"),
        created_at=_parse_datetime(row["created_at"], "created_at"),
        source=_parse_enum(EntrySource, row["source"], "source"),
        description=row["description"].strip(),
        prepared_by=_required(row["prepared_by"], "prepared_by"),
        approved_by=_optional(row["approved_by"]),
        currency=_required(row["currency"], "currency"),
    )
    line = JournalEntryLine(
        line_number=_parse_int(row["line_number"], "line_number"),
        account_number=_required(row["account_number"], "account_number"),
        debit=_parse_amount(row["debit"], "debit"),
        credit=_parse_amount(row["credit"], "credit"),
        department=_optional(row["department"]),
        vendor=_optional(row["vendor"]),
    )
    return header, line


def _require_columns(fieldnames, required) -> None:
    missing = [name for name in required if name not in (fieldnames or [])]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")


def _required(value: str, name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{name} is blank")
    return value


def _optional(value: str) -> str | None:
    return value.strip() or None


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ValueError(f"{name} is not a whole number: {value!r}") from None


def _parse_amount(value: str, name: str) -> Decimal:
    text = value.strip()
    if not text:
        return Decimal("0")
    try:
        amount = Decimal(text)  # parsed from text, never through float
    except InvalidOperation:
        raise ValueError(f"{name} is not a valid amount: {value!r}") from None
    if not amount.is_finite():  # Decimal accepts "NaN" and "Infinity"
        raise ValueError(f"{name} is not a valid amount: {value!r}")
    return amount


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{name} is not a valid date (YYYY-MM-DD): {value!r}") from None


def _parse_datetime(value: str, name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{name} is not a valid timestamp (YYYY-MM-DD HH:MM:SS): {value!r}") from None


def _parse_enum(enum_type: type[StrEnum], value: str, name: str) -> StrEnum:
    try:
        return enum_type(value.strip().upper())
    except ValueError:
        raise ValueError(f"{name} must be one of {', '.join(enum_type)}: {value!r}") from None
