"""Read queries used by the API."""

from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import JournalEntryLineRecord, JournalEntryRecord, RiskRun, RuleHitRecord

# Columns the entry list can be sorted by.
SORT_COLUMNS = {
    "risk_score": JournalEntryRecord.risk_score,
    "amount": JournalEntryRecord.amount,
    "posting_date": JournalEntryRecord.posting_date,
    "entry_id": JournalEntryRecord.entry_id,
}


@dataclass
class EntryFilters:
    risk_level: str | None = None
    rule_code: str | None = None
    prepared_by: str | None = None
    account_number: str | None = None
    entry_id: str | None = None


def list_runs(session: Session) -> list[RiskRun]:
    return list(session.scalars(select(RiskRun).order_by(RiskRun.id.desc())))


def get_run(session: Session, run_id: int) -> RiskRun | None:
    return session.get(RiskRun, run_id)


def level_counts(session: Session, run_ids: list[int]) -> dict[int, dict[str, int]]:
    """Entries per risk level for each run, in one query (not one query per run)."""
    counts = {run_id: {"HIGH": 0, "MEDIUM": 0, "LOW": 0} for run_id in run_ids}
    rows = session.execute(
        select(JournalEntryRecord.run_id, JournalEntryRecord.risk_level, func.count())
        .where(JournalEntryRecord.run_id.in_(run_ids))
        .group_by(JournalEntryRecord.run_id, JournalEntryRecord.risk_level)
    )
    for run_id, risk_level, count in rows:
        counts[run_id][risk_level] = count
    return counts


def list_entries(
    session: Session,
    run_id: int,
    filters: EntryFilters,
    sort: str = "-risk_score",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[JournalEntryRecord], int]:
    """One page of a run's entries, plus the total number that match the filters.

    `sort` is a key of SORT_COLUMNS; a leading "-" sorts in descending order.
    """
    conditions = [JournalEntryRecord.run_id == run_id]
    if filters.risk_level:
        conditions.append(JournalEntryRecord.risk_level == filters.risk_level)
    if filters.prepared_by:
        conditions.append(JournalEntryRecord.prepared_by == filters.prepared_by)
    if filters.entry_id:
        conditions.append(JournalEntryRecord.entry_id == filters.entry_id)
    if filters.rule_code:
        conditions.append(
            exists().where(
                RuleHitRecord.journal_entry_id == JournalEntryRecord.id,
                RuleHitRecord.rule_code == filters.rule_code,
            )
        )
    if filters.account_number:
        conditions.append(
            exists().where(
                JournalEntryLineRecord.journal_entry_id == JournalEntryRecord.id,
                JournalEntryLineRecord.account_number == filters.account_number,
            )
        )

    total = session.scalar(select(func.count()).select_from(JournalEntryRecord).where(*conditions))

    column = SORT_COLUMNS[sort.removeprefix("-")]
    order = column.desc() if sort.startswith("-") else column.asc()
    query = (
        select(JournalEntryRecord)
        .where(*conditions)
        # entry_id and id break ties, so pages are stable and no entry appears on two pages.
        .order_by(order, JournalEntryRecord.entry_id, JournalEntryRecord.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .options(selectinload(JournalEntryRecord.hits))  # rule codes for the whole page in one extra query
    )
    return list(session.scalars(query)), total


def get_entry(session: Session, entry_db_id: int) -> JournalEntryRecord | None:
    query = (
        select(JournalEntryRecord)
        .where(JournalEntryRecord.id == entry_db_id)
        .options(selectinload(JournalEntryRecord.lines), selectinload(JournalEntryRecord.hits))
    )
    return session.scalars(query).one_or_none()
