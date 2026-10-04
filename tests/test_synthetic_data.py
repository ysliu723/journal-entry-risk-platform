"""The synthetic ledger must be internally consistent, or the evaluation means nothing."""

import json

from app.config import load_config
from app.ingestion.csv_loader import GL_DETAIL_COLUMNS, TRIAL_BALANCE_COLUMNS
from app.services.analysis import analyze_files
from scripts.generate_synthetic_data import damage, generate, gl_rows, trial_balance_rows, write_csv


def write_ledger(tmp_path, entry_count=3000, seed=7):
    entries = generate(entry_count, seed)
    rows = gl_rows(entries)
    write_csv(tmp_path / "gl.csv", GL_DETAIL_COLUMNS, rows)
    write_csv(tmp_path / "tb.csv", TRIAL_BALANCE_COLUMNS, trial_balance_rows(entries))
    config = {"fiscal_year_end": "2026-12-31", "period_close_date": "2027-01-10"}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return entries, rows


def test_generated_ledger_is_complete_and_consistent(tmp_path):
    entries, _ = write_ledger(tmp_path)

    analysis = analyze_files(tmp_path / "gl.csv", tmp_path / "tb.csv", load_config(tmp_path / "config.json"))

    assert len(entries) == 3000
    assert analysis.load.errors == []
    assert analysis.findings == []  # balanced, unique, no gaps, TB rolls forward
    assert {entry.scenario for entry in entries if entry.scenario} == {
        "YEAR_END_REVENUE_TOPSIDE",
        "THRESHOLD_AVOIDANCE",
        "SELF_APPROVED_PAYMENT",
        "SUSPENSE_ACCOUNT",
        "FICTITIOUS_VENDOR",
    }


def test_same_seed_gives_the_same_ledger():
    first = gl_rows(generate(500, seed=3))
    second = gl_rows(generate(500, seed=3))
    assert first == second


def test_every_defect_is_caught_by_the_integrity_checks(tmp_path):
    entries, rows = write_ledger(tmp_path)
    damaged, defects = damage(rows, entries, seed=7)
    write_csv(tmp_path / "damaged.csv", GL_DETAIL_COLUMNS, damaged)

    analysis = analyze_files(tmp_path / "damaged.csv", tmp_path / "tb.csv", load_config(tmp_path / "config.json"))

    assert [error.entry_id for error in analysis.load.errors] == [defects["MALFORMED_ROW"]]
    found = {(finding.check, finding.entry_id) for finding in analysis.findings}
    assert ("UNBALANCED_ENTRY", defects["UNBALANCED_ENTRY"]) in found
    assert ("DUPLICATE_ENTRY_ID", defects["DUPLICATE_ENTRY_ID"]) in found
    gap_messages = " ".join(f.message for f in analysis.findings if f.check == "SEQUENCE_GAP")
    assert defects["DELETED_ENTRY"] in gap_messages
    assert defects["MALFORMED_ROW"] in gap_messages
    assert any(finding.check == "TB_ROLLFORWARD" for finding in analysis.findings)
