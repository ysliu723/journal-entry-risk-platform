"""The whole Phase 1 pipeline on the hand-made sample ledger."""

import re
from pathlib import Path

from app.cli import main
from app.config import load_config
from app.services.analysis import analyze_files

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
GL = SAMPLE_DIR / "gl_detail.csv"
TB = SAMPLE_DIR / "trial_balance.csv"
CONFIG = SAMPLE_DIR / "client_config.json"


def sample_analysis():
    return analyze_files(GL, TB, load_config(CONFIG))


def test_sample_ranking():
    analysis = sample_analysis()
    ranking = [(r.entry_id, r.risk_score, str(r.risk_level)) for r in analysis.results]
    assert ranking[:7] == [
        ("JE000013", 140, "HIGH"),  # year-end adjustment by the controller, late at night, no approver
        ("JE000007", 70, "HIGH"),  # 97,500 with no approver, just under the 100,000 threshold
        ("JE000010", 70, "HIGH"),  # "Misc expense" to a seldom-used account by ops_mgr
        ("JE000014", 70, "HIGH"),  # revenue keyed in after close, backdated to December 30
        ("JE000009", 40, "MEDIUM"),  # prepared by gl_acct1, approved by GL_ACCT1
        ("JE000006", 20, "LOW"),  # round payment on a Saturday
        ("JE000001", 10, "LOW"),  # round accrual
    ]
    assert all(result.risk_score == 0 for result in analysis.results[7:])


def test_showcase_entry_explains_every_point():
    results = {result.entry_id: result for result in sample_analysis().results}
    hits = [(hit.rule_code, hit.score) for hit in results["JE000013"].hits]
    assert hits == [
        ("MISSING_APPROVAL", 40),
        ("NEAR_APPROVAL_THRESHOLD", 30),
        ("INFREQUENT_PREPARER", 20),
        ("LATE_NIGHT_ENTRY", 20),
        ("PERIOD_END_ENTRY", 20),
        ("VAGUE_DESCRIPTION", 10),
    ]


def test_system_batch_jobs_are_not_flagged_for_timing():
    # JE000011 is the payroll batch, posted at 01:30 on December 31.
    results = {result.entry_id: result for result in sample_analysis().results}
    assert results["JE000011"].hits == ()


def test_cli_report(capsys):
    exit_code = main([str(GL), str(TB), "--config", str(CONFIG)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Loaded 14 entries; rejected 0 invalid row(s)." in out
    assert "Integrity checks: passed." in out
    assert re.search(r"JE000013\s+\|\s+HIGH\s+\|\s+140\s+\|\s+MISSING_APPROVAL, NEAR_APPROVAL_THRESHOLD", out)
    assert "HIGH 4 | MEDIUM 1 | LOW 9" in out


def test_cli_warns_when_the_population_is_incomplete(tmp_path, capsys):
    # Drop the December payroll entry from the export.
    rows = GL.read_text(encoding="utf-8").splitlines(keepends=True)
    gl = tmp_path / "gl.csv"
    gl.write_text("".join(row for row in rows if not row.startswith("JE000011,")), encoding="utf-8")

    main([str(gl), str(TB), "--config", str(CONFIG)])
    out = capsys.readouterr().out

    assert "Integrity checks: 4 finding(s). The population may be incomplete;" in out
    assert "SEQUENCE_GAP" in out
    assert "TB_ROLLFORWARD" in out


def test_cli_reports_a_bad_config(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text('{"fiscal_year_end": "2026-12-31"}', encoding="utf-8")

    assert main([str(GL), str(TB), "--config", str(config)]) == 1
    assert "period_close_date" in capsys.readouterr().err
