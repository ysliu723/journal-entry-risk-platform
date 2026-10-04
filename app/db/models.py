"""Database tables.

Everything belongs to a risk run: one upload of a GL export and trial
balance, scored with one config. A saved run never changes, so any past
result can be reproduced and explained later.

    risk_runs ─┬─ load_errors            rows rejected while loading
               ├─ integrity_findings     balance, duplicates, gaps, TB rollforward
               ├─ trial_balance_lines
               └─ journal_entries ─┬─ journal_entry_lines
                                   └─ rule_hits
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Exact money: 18 digits in total, 2 after the decimal point.
Money = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class RiskRun(Base):
    __tablename__ = "risk_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    gl_filename: Mapped[str] = mapped_column(String(255))
    trial_balance_filename: Mapped[str] = mapped_column(String(255))
    rule_set_version: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB)
    entry_count: Mapped[int]
    rejected_row_count: Mapped[int]
    integrity_passed: Mapped[bool]

    load_errors: Mapped[list["LoadErrorRecord"]] = relationship(order_by="LoadErrorRecord.row_number")
    integrity_findings: Mapped[list["IntegrityFindingRecord"]] = relationship(order_by="IntegrityFindingRecord.id")


class LoadErrorRecord(Base):
    __tablename__ = "load_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("risk_runs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int]
    entry_id: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


class IntegrityFindingRecord(Base):
    __tablename__ = "integrity_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("risk_runs.id", ondelete="CASCADE"), index=True)
    check_code: Mapped[str] = mapped_column(String(32))
    entry_id: Mapped[str | None] = mapped_column(String(64))
    account_number: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)


class TrialBalanceRecord(Base):
    __tablename__ = "trial_balance_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("risk_runs.id", ondelete="CASCADE"), index=True)
    account_number: Mapped[str] = mapped_column(String(32))
    account_name: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(16))
    opening_balance: Mapped[Decimal] = mapped_column(Money)
    closing_balance: Mapped[Decimal] = mapped_column(Money)


class JournalEntryRecord(Base):
    __tablename__ = "journal_entries"

    # Not entry_id: the same entry ID can appear twice in one export (a finding we keep).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("risk_runs.id", ondelete="CASCADE"))
    entry_id: Mapped[str] = mapped_column(String(64))
    posting_date: Mapped[date]
    created_at: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    prepared_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Money)
    risk_score: Mapped[int]
    risk_level: Mapped[str] = mapped_column(String(8))

    lines: Mapped[list["JournalEntryLineRecord"]] = relationship(order_by="JournalEntryLineRecord.line_number")
    hits: Mapped[list["RuleHitRecord"]] = relationship(order_by="RuleHitRecord.id")

    @property
    def rule_codes(self) -> list[str]:
        return [hit.rule_code for hit in self.hits]


class JournalEntryLineRecord(Base):
    __tablename__ = "journal_entry_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    line_number: Mapped[int]
    account_number: Mapped[str] = mapped_column(String(32))
    debit: Mapped[Decimal] = mapped_column(Money)
    credit: Mapped[Decimal] = mapped_column(Money)
    department: Mapped[str | None] = mapped_column(String(64))
    vendor: Mapped[str | None] = mapped_column(String(255))


class RuleHitRecord(Base):
    __tablename__ = "rule_hits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    rule_code: Mapped[str] = mapped_column(String(32))
    rule_version: Mapped[int]
    score: Mapped[int]
    reason: Mapped[str] = mapped_column(Text)


# A run's entries filtered by level, highest score first, and the counts per level.
# The columns and directions match the ORDER BY (risk_score DESC, entry_id, id),
# so PostgreSQL can read rows already in order instead of sorting them.
Index(
    "ix_journal_entries_run_level_score",
    JournalEntryRecord.run_id,
    JournalEntryRecord.risk_level,
    JournalEntryRecord.risk_score.desc(),
    JournalEntryRecord.entry_id,
    JournalEntryRecord.id,
)
# The default list (no level filter), highest score first. The index above cannot serve it,
# because risk_level comes before risk_score; see docs/benchmarks.md.
Index(
    "ix_journal_entries_run_score",
    JournalEntryRecord.run_id,
    JournalEntryRecord.risk_score.desc(),
    JournalEntryRecord.entry_id,
    JournalEntryRecord.id,
)
# Looking up an entry number within a run.
Index("ix_journal_entries_run_entry_id", JournalEntryRecord.run_id, JournalEntryRecord.entry_id)
# "Which entries touched account X?" and "Which entries triggered rule Y?"
Index("ix_lines_account_entry", JournalEntryLineRecord.account_number, JournalEntryLineRecord.journal_entry_id)
Index("ix_rule_hits_rule_entry", RuleHitRecord.rule_code, RuleHitRecord.journal_entry_id)
