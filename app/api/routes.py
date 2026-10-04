"""HTTP endpoints.

    POST /runs                       upload a GL export, trial balance, and config; returns the new run
    GET  /runs                       all runs, newest first
    GET  /runs/{run_id}              one run: counts, rejected rows, integrity findings
    GET  /runs/{run_id}/entries      the run's entries: filter, sort, paginate
    GET  /entries/{id}               one entry with its lines and the reason for every point
"""

import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.schemas import EntryDetail, EntryPage, EntrySummary, RunDetail, RunSummary
from app.config import ClientConfig
from app.db.database import get_session
from app.db.models import RiskRun
from app.db.persistence import save_run
from app.db.queries import EntryFilters, get_entry, get_run, level_counts, list_entries, list_runs
from app.engine.risk_engine import RiskLevel
from app.ingestion.csv_loader import CSV_ENCODING
from app.services.analysis import analyze

router = APIRouter()

SortOption = Literal[
    "-risk_score", "risk_score", "-amount", "amount", "-posting_date", "posting_date", "-entry_id", "entry_id"
]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def create_run(
    gl_detail: UploadFile = File(description="GL detail export (CSV, one row per entry line)"),
    trial_balance: UploadFile = File(description="trial balance (CSV)"),
    config: UploadFile = File(description="client config (JSON)"),
    session: Session = Depends(get_session),
):
    """Load, check, score, and save a run. Runs synchronously; large files take a few seconds."""
    try:
        client_config = ClientConfig.from_dict(json.load(config.file))
        analysis = analyze(_as_text(gl_detail), _as_text(trial_balance), client_config)
    except (ValueError, UnicodeDecodeError) as exc:  # json.JSONDecodeError is a ValueError too
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None

    run = save_run(session, analysis, gl_detail.filename or "gl_detail.csv", trial_balance.filename or "trial_balance.csv")
    session.commit()
    return _run_detail(session, run)


@router.get("/runs", response_model=list[RunSummary])
def get_runs(session: Session = Depends(get_session)):
    runs = list_runs(session)
    counts = level_counts(session, [run.id for run in runs])
    return [_run_summary(run, counts[run.id]) for run in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run_detail(run_id: int, session: Session = Depends(get_session)):
    return _run_detail(session, _find_run(session, run_id))


@router.get("/runs/{run_id}/entries", response_model=EntryPage)
def get_run_entries(
    run_id: int,
    risk_level: RiskLevel | None = None,
    rule_code: str | None = None,
    prepared_by: str | None = None,
    account_number: str | None = None,
    entry_id: str | None = None,
    sort: SortOption = "-risk_score",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    _find_run(session, run_id)
    filters = EntryFilters(
        risk_level=str(risk_level) if risk_level else None,
        rule_code=rule_code,
        prepared_by=prepared_by,
        account_number=account_number,
        entry_id=entry_id,
    )
    records, total = list_entries(session, run_id, filters, sort, page, page_size)
    items = [EntrySummary.model_validate(record) for record in records]
    return EntryPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/entries/{entry_db_id}", response_model=EntryDetail)
def get_entry_detail(entry_db_id: int, session: Session = Depends(get_session)):
    record = get_entry(session, entry_db_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"entry {entry_db_id} not found")
    return EntryDetail.model_validate(record)


def _as_text(upload: UploadFile) -> io.TextIOWrapper:
    """An uploaded file arrives as bytes; read it as text, the way open(path) would."""
    return io.TextIOWrapper(upload.file, encoding=CSV_ENCODING, newline="")


def _find_run(session: Session, run_id: int) -> RiskRun:
    run = get_run(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"run {run_id} not found")
    return run


def _run_summary(run: RiskRun, counts: dict[str, int]) -> RunSummary:
    return RunSummary(
        id=run.id,
        created_at=run.created_at,
        gl_filename=run.gl_filename,
        trial_balance_filename=run.trial_balance_filename,
        rule_set_version=run.rule_set_version,
        entry_count=run.entry_count,
        rejected_row_count=run.rejected_row_count,
        integrity_passed=run.integrity_passed,
        level_counts=counts,
    )


def _run_detail(session: Session, run: RiskRun) -> RunDetail:
    summary = _run_summary(run, level_counts(session, [run.id])[run.id])
    return RunDetail(
        **summary.model_dump(),
        config=run.config,
        load_errors=run.load_errors,
        integrity_findings=run.integrity_findings,
    )
