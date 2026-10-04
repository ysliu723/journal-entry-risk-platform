"""Save a RunAnalysis to the database.

A run can hold 100,000+ entries, so there are three ways to insert entries,
lines, and rule hits (scripts/benchmark.py compares them):

- "orm":  build ORM objects and let SQLAlchemy insert them
- "core": one bulk INSERT per table, sending all rows at once
- "copy": PostgreSQL COPY, which streams rows straight into the table

"copy" is the default because it was the fastest in the benchmark.
"""

from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from app.db.models import (
    IntegrityFindingRecord,
    JournalEntryLineRecord,
    JournalEntryRecord,
    LoadErrorRecord,
    RiskRun,
    RuleHitRecord,
    TrialBalanceRecord,
)
from app.services.analysis import RunAnalysis

LOAD_METHODS = ("orm", "core", "copy")

ENTRY_COLUMNS = (
    "id",
    "run_id",
    "entry_id",
    "posting_date",
    "created_at",
    "source",
    "description",
    "prepared_by",
    "approved_by",
    "currency",
    "amount",
    "risk_score",
    "risk_level",
)
LINE_COLUMNS = ("journal_entry_id", "line_number", "account_number", "debit", "credit", "department", "vendor")
HIT_COLUMNS = ("journal_entry_id", "rule_code", "rule_version", "score", "reason")


def save_run(
    session: Session,
    analysis: RunAnalysis,
    gl_filename: str,
    trial_balance_filename: str,
    method: str = "copy",
) -> RiskRun:
    """Insert a run and everything in it.

    The caller commits. Until then nothing is visible to anyone else, so a
    run is saved completely or not at all.
    """
    if method not in LOAD_METHODS:
        raise ValueError(f"method must be one of {', '.join(LOAD_METHODS)}")

    run = RiskRun(
        gl_filename=gl_filename,
        trial_balance_filename=trial_balance_filename,
        rule_set_version=analysis.rule_set_version,
        config=analysis.config.to_dict(),
        entry_count=len(analysis.results),
        rejected_row_count=len(analysis.load.errors),
        integrity_passed=analysis.integrity_passed,
    )
    session.add(run)
    session.flush()  # sends the INSERT now, so run.id is known

    for error in analysis.load.errors:
        session.add(
            LoadErrorRecord(run_id=run.id, row_number=error.row_number, entry_id=error.entry_id, message=error.message)
        )
    for finding in analysis.findings:
        session.add(
            IntegrityFindingRecord(
                run_id=run.id,
                check_code=finding.check,
                entry_id=finding.entry_id,
                account_number=finding.account_number,
                message=finding.message,
            )
        )
    for tb_line in analysis.trial_balance:
        session.add(
            TrialBalanceRecord(
                run_id=run.id,
                account_number=tb_line.account_number,
                account_name=tb_line.account_name,
                account_type=str(tb_line.account_type),
                opening_balance=tb_line.opening_balance,
                closing_balance=tb_line.closing_balance,
            )
        )
    session.flush()

    if method == "orm":
        _save_entries_orm(session, run.id, analysis)
    else:
        entry_rows, line_rows, hit_rows = _build_rows(session, run.id, analysis)
        if method == "core":
            _insert_core(session, entry_rows, line_rows, hit_rows)
        else:
            _insert_copy(session, entry_rows, line_rows, hit_rows)
    return run


def _save_entries_orm(session: Session, run_id: int, analysis: RunAnalysis) -> None:
    for result in analysis.results:
        entry = result.entry
        record = JournalEntryRecord(
            run_id=run_id,
            entry_id=entry.entry_id,
            posting_date=entry.posting_date,
            created_at=entry.created_at,
            source=str(entry.source),
            description=entry.description,
            prepared_by=entry.prepared_by,
            approved_by=entry.approved_by,
            currency=entry.currency,
            amount=entry.amount,
            risk_score=result.risk_score,
            risk_level=str(result.risk_level),
        )
        for line in entry.lines:
            record.lines.append(
                JournalEntryLineRecord(
                    line_number=line.line_number,
                    account_number=line.account_number,
                    debit=line.debit,
                    credit=line.credit,
                    department=line.department,
                    vendor=line.vendor,
                )
            )
        for hit in result.hits:
            record.hits.append(
                RuleHitRecord(rule_code=hit.rule_code, rule_version=hit.rule_version, score=hit.score, reason=hit.reason)
            )
        session.add(record)
    session.flush()


def _build_rows(session: Session, run_id: int, analysis: RunAnalysis):
    """Turn the analysis into plain row tuples, in the column order above."""
    database_ids = _reserve_entry_ids(session, len(analysis.results))
    entry_rows, line_rows, hit_rows = [], [], []
    for database_id, result in zip(database_ids, analysis.results):
        entry = result.entry
        entry_rows.append(
            (
                database_id,
                run_id,
                entry.entry_id,
                entry.posting_date,
                entry.created_at,
                str(entry.source),
                entry.description,
                entry.prepared_by,
                entry.approved_by,
                entry.currency,
                entry.amount,
                result.risk_score,
                str(result.risk_level),
            )
        )
        for line in entry.lines:
            line_rows.append(
                (database_id, line.line_number, line.account_number, line.debit, line.credit, line.department, line.vendor)
            )
        for hit in result.hits:
            hit_rows.append((database_id, hit.rule_code, hit.rule_version, hit.score, hit.reason))
    return entry_rows, line_rows, hit_rows


def _reserve_entry_ids(session: Session, count: int) -> list[int]:
    """Take `count` ids from the journal_entries id sequence in a single query.

    With the ids known up front, lines and rule hits can point at their entry
    without asking the database for each new entry's id.
    """
    if count == 0:
        return []
    rows = session.execute(
        text("SELECT nextval(pg_get_serial_sequence('journal_entries', 'id')) FROM generate_series(1, :count)"),
        {"count": count},
    )
    return [row[0] for row in rows]


def _insert_core(session: Session, entry_rows, line_rows, hit_rows) -> None:
    tables = [
        (JournalEntryRecord.__table__, ENTRY_COLUMNS, entry_rows),
        (JournalEntryLineRecord.__table__, LINE_COLUMNS, line_rows),
        (RuleHitRecord.__table__, HIT_COLUMNS, hit_rows),
    ]
    for table, columns, rows in tables:
        if rows:
            session.execute(insert(table), [dict(zip(columns, row)) for row in rows])


def _insert_copy(session: Session, entry_rows, line_rows, hit_rows) -> None:
    # COPY is PostgreSQL-specific, so we use the psycopg connection underneath
    # SQLAlchemy. It is the same connection, inside the same transaction.
    connection = session.connection().connection.driver_connection
    with connection.cursor() as cursor:
        _copy_rows(cursor, "journal_entries", ENTRY_COLUMNS, entry_rows)
        _copy_rows(cursor, "journal_entry_lines", LINE_COLUMNS, line_rows)
        _copy_rows(cursor, "rule_hits", HIT_COLUMNS, hit_rows)


def _copy_rows(cursor, table: str, columns: tuple[str, ...], rows) -> None:
    with cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
