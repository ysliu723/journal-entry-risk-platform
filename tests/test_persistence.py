from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import load_config
from app.db.models import JournalEntryLineRecord, JournalEntryRecord, RiskRun
from app.db.persistence import LOAD_METHODS, save_run
from app.services.analysis import analyze_files

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
GL = SAMPLE_DIR / "gl_detail.csv"
TB = SAMPLE_DIR / "trial_balance.csv"


def sample_analysis(gl=GL):
    return analyze_files(gl, TB, load_config(SAMPLE_DIR / "client_config.json"))


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("method", LOAD_METHODS)
def test_every_load_method_saves_the_same_run(db_session, method):
    run = save_run(db_session, sample_analysis(), "gl_detail.csv", "trial_balance.csv", method=method)
    db_session.commit()

    assert run.entry_count == 14
    assert run.integrity_passed is True
    assert run.config["fiscal_year_end"] == "2026-12-31"
    assert "SELF_APPROVAL@1" in run.rule_set_version
    assert count(db_session, JournalEntryRecord) == 14
    assert count(db_session, JournalEntryLineRecord) == 30

    showcase = db_session.scalars(select(JournalEntryRecord).where(JournalEntryRecord.entry_id == "JE000013")).one()
    assert (showcase.risk_score, showcase.risk_level) == (140, "HIGH")
    assert showcase.amount == Decimal("99800.00")
    assert [line.account_number for line in showcase.lines] == ["610100", "200100"]
    assert [hit.rule_code for hit in showcase.hits] == [
        "MISSING_APPROVAL",
        "NEAR_APPROVAL_THRESHOLD",
        "INFREQUENT_PREPARER",
        "LATE_NIGHT_ENTRY",
        "PERIOD_END_ENTRY",
        "VAGUE_DESCRIPTION",
    ]


def test_saves_rejected_rows_and_integrity_findings(db_session, tmp_path):
    rows = GL.read_text(encoding="utf-8").splitlines(keepends=True)
    rows = [row for row in rows if not row.startswith("JE000011,")]  # drop one entry
    rows[1] = rows[1].replace("2026-03-31", "2026-03-32", 1)  # break one row of JE000001
    gl = tmp_path / "gl.csv"
    gl.write_text("".join(rows), encoding="utf-8")

    run = save_run(db_session, sample_analysis(gl), "gl.csv", "trial_balance.csv")
    db_session.commit()

    assert run.integrity_passed is False
    assert run.rejected_row_count == 1
    assert [(e.row_number, e.entry_id) for e in run.load_errors] == [(2, "JE000001")]
    assert {f.check_code for f in run.integrity_findings} == {"SEQUENCE_GAP", "TB_ROLLFORWARD"}


def test_nothing_is_saved_until_commit(db_session):
    save_run(db_session, sample_analysis(), "gl_detail.csv", "trial_balance.csv")
    db_session.rollback()  # e.g. something failed halfway through
    assert count(db_session, RiskRun) == 0
    assert count(db_session, JournalEntryRecord) == 0


def test_rejects_an_unknown_load_method(db_session):
    with pytest.raises(ValueError, match="method must be one of"):
        save_run(db_session, sample_analysis(), "gl_detail.csv", "trial_balance.csv", method="fast")
