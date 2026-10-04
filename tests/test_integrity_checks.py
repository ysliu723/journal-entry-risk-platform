from pathlib import Path

from app.ingestion.csv_loader import load_gl_detail, load_trial_balance
from app.integrity.checks import (
    DUPLICATE_ENTRY_ID,
    SEQUENCE_GAP,
    TB_ROLLFORWARD,
    UNBALANCED_ENTRY,
    check_balanced,
    check_duplicate_entry_ids,
    check_sequence_gaps,
    check_tb_rollforward,
    run_all_checks,
)
from tests.factories import entry_with_amount, line, make_entry, tb_line

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def load_sample():
    entries = load_gl_detail(SAMPLE_DIR / "gl_detail.csv").entries
    trial_balance = load_trial_balance(SAMPLE_DIR / "trial_balance.csv")
    return entries, trial_balance


def test_sample_population_passes_all_checks():
    entries, trial_balance = load_sample()
    assert run_all_checks(entries, trial_balance) == []


def test_unbalanced_entry_is_reported():
    entries = [
        entry_with_amount("100.00", entry_id="JE000001"),
        make_entry(line(1, "610100", debit="100.00"), line(2, "200100", credit="90.00"), entry_id="JE000002"),
    ]
    [finding] = check_balanced(entries)
    assert finding.check == UNBALANCED_ENTRY
    assert finding.entry_id == "JE000002"
    assert "100.00" in finding.message and "90.00" in finding.message


def test_duplicate_entry_id_is_reported():
    entries = [
        entry_with_amount("100.00", entry_id="JE000001"),
        entry_with_amount("100.00", entry_id="JE000001"),
        entry_with_amount("5.00", entry_id="JE000002"),
    ]
    [finding] = check_duplicate_entry_ids(entries)
    assert finding.check == DUPLICATE_ENTRY_ID
    assert finding.entry_id == "JE000001"
    assert "2 separate entries" in finding.message


def test_sequence_gaps_are_reported_as_ranges():
    ids = ["JE000001", "JE000002", "JE000004", "JE000008"]
    findings = check_sequence_gaps([entry_with_amount("1.00", entry_id=entry_id) for entry_id in ids])
    assert [finding.check for finding in findings] == [SEQUENCE_GAP, SEQUENCE_GAP]
    assert [finding.message for finding in findings] == [
        "Missing 1 entry ID(s): JE000003.",
        "Missing 3 entry ID(s): JE000005 to JE000007.",
    ]


def test_sequence_gaps_are_checked_per_prefix_and_skip_non_numeric_ids():
    ids = ["JE000001", "JE000002", "AJE01", "AJE03", "MANUAL-A"]
    findings = check_sequence_gaps([entry_with_amount("1.00", entry_id=entry_id) for entry_id in ids])
    assert [finding.message for finding in findings] == ["Missing 1 entry ID(s): AJE02."]


class TestTbRollforward:
    def test_passes_when_gl_activity_explains_the_movement(self):
        entries = [entry_with_amount("250.00")]  # Dr 610100 / Cr 200100
        trial_balance = [tb_line("610100", "1000.00", "1250.00"), tb_line("200100", "-1000.00", "-1250.00")]
        assert check_tb_rollforward(entries, trial_balance) == []

    def test_missing_activity_means_the_population_is_incomplete(self):
        # The TB moved by 250.00 but the GL export we received has no entries.
        trial_balance = [tb_line("610100", "1000.00", "1250.00"), tb_line("200100", "-1000.00", "-1250.00")]
        findings = check_tb_rollforward([], trial_balance)
        assert [finding.account_number for finding in findings] == ["610100", "200100"]
        assert "unexplained difference 250.00" in findings[0].message

    def test_account_with_activity_but_missing_from_trial_balance(self):
        entries = [make_entry(line(1, "999999", debit="80.00"), line(2, "200100", credit="80.00"))]
        trial_balance = [tb_line("200100", "0.00", "-80.00")]
        [finding] = check_tb_rollforward(entries, trial_balance)
        assert finding.check == TB_ROLLFORWARD
        assert finding.account_number == "999999"
        assert "not in the trial balance" in finding.message


def test_removing_one_sample_entry_is_caught_twice():
    entries, trial_balance = load_sample()
    without_december_payroll = [entry for entry in entries if entry.entry_id != "JE000011"]

    findings = run_all_checks(without_december_payroll, trial_balance)

    assert {(f.check, f.entry_id or f.account_number) for f in findings} == {
        (SEQUENCE_GAP, None),
        (TB_ROLLFORWARD, "100100"),
        (TB_ROLLFORWARD, "220100"),
        (TB_ROLLFORWARD, "620100"),
    }
