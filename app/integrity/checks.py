"""Data integrity checks, run before any risk scoring.

They answer one question: can this journal entry population be relied on?
A failed check does not change risk scores, but every result of the run
must be read with that caveat.
"""

import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from app.domain.journal_entry import JournalEntry
from app.domain.trial_balance import TrialBalanceLine

UNBALANCED_ENTRY = "UNBALANCED_ENTRY"
DUPLICATE_ENTRY_ID = "DUPLICATE_ENTRY_ID"
SEQUENCE_GAP = "SEQUENCE_GAP"
TB_ROLLFORWARD = "TB_ROLLFORWARD"

# An entry ID made of a text prefix and a number, such as "JE000123".
_SEQUENTIAL_ID = re.compile(r"^(\D*)(\d+)$")


@dataclass(frozen=True)
class IntegrityFinding:
    check: str
    message: str
    entry_id: str | None = None
    account_number: str | None = None


def run_all_checks(entries: list[JournalEntry], trial_balance: list[TrialBalanceLine]) -> list[IntegrityFinding]:
    findings = []
    findings.extend(check_balanced(entries))
    findings.extend(check_duplicate_entry_ids(entries))
    findings.extend(check_sequence_gaps(entries))
    findings.extend(check_tb_rollforward(entries, trial_balance))
    return findings


def check_balanced(entries: list[JournalEntry]) -> list[IntegrityFinding]:
    findings = []
    for entry in entries:
        if not entry.is_balanced:
            findings.append(
                IntegrityFinding(
                    UNBALANCED_ENTRY,
                    f"Debits {entry.total_debit:,.2f} do not equal credits {entry.total_credit:,.2f}.",
                    entry_id=entry.entry_id,
                )
            )
    return findings


def check_duplicate_entry_ids(entries: list[JournalEntry]) -> list[IntegrityFinding]:
    counts = Counter(entry.entry_id for entry in entries)
    findings = []
    for entry_id, count in counts.items():
        if count > 1:
            findings.append(
                IntegrityFinding(DUPLICATE_ENTRY_ID, f"Entry ID is used by {count} separate entries.", entry_id=entry_id)
            )
    return findings


def check_sequence_gaps(entries: list[JournalEntry]) -> list[IntegrityFinding]:
    """Report missing numbers in sequential entry IDs such as JE000001, JE000002, ...

    A gap can mean an entry was deleted or left out of the export. IDs without
    a numeric part are skipped. Consecutive missing numbers are reported as one range.
    """
    numbers_by_prefix: dict[str, set[int]] = {}
    width_by_prefix: dict[str, int] = {}
    for entry in entries:
        match = _SEQUENTIAL_ID.match(entry.entry_id)
        if match is None:
            continue
        prefix, digits = match.group(1), match.group(2)
        if prefix not in numbers_by_prefix:
            numbers_by_prefix[prefix] = set()
            width_by_prefix[prefix] = len(digits)
        numbers_by_prefix[prefix].add(int(digits))

    findings = []
    for prefix, numbers in numbers_by_prefix.items():
        width = width_by_prefix[prefix]
        ordered = sorted(numbers)
        # Compare each number with the next one: 3 followed by 7 means 4-6 are missing.
        for previous, current in zip(ordered, ordered[1:]):
            missing = current - previous - 1
            if missing == 0:
                continue
            first = f"{prefix}{previous + 1:0{width}d}"
            last = f"{prefix}{current - 1:0{width}d}"
            ids = first if missing == 1 else f"{first} to {last}"
            findings.append(IntegrityFinding(SEQUENCE_GAP, f"Missing {missing} entry ID(s): {ids}."))
    return findings


def check_tb_rollforward(
    entries: list[JournalEntry], trial_balance: list[TrialBalanceLine]
) -> list[IntegrityFinding]:
    """Check that opening balance + GL activity = closing balance for every account.

    This is how an auditor confirms the journal entry population is complete
    before testing it. It assumes the entries cover exactly the period between
    the opening and closing balances.
    """
    activity: dict[str, Decimal] = {}
    for entry in entries:
        for line in entry.lines:
            so_far = activity.get(line.account_number, Decimal("0"))
            activity[line.account_number] = so_far + line.debit - line.credit

    findings = []
    tb_accounts = set()
    for tb_line in trial_balance:
        tb_accounts.add(tb_line.account_number)
        net_activity = activity.get(tb_line.account_number, Decimal("0"))
        expected_closing = tb_line.opening_balance + net_activity
        if expected_closing != tb_line.closing_balance:
            difference = tb_line.closing_balance - expected_closing
            findings.append(
                IntegrityFinding(
                    TB_ROLLFORWARD,
                    f"Opening {tb_line.opening_balance:,.2f} + GL activity {net_activity:,.2f} "
                    f"= {expected_closing:,.2f}, but the closing balance is {tb_line.closing_balance:,.2f} "
                    f"(unexplained difference {difference:,.2f}).",
                    account_number=tb_line.account_number,
                )
            )

    for account_number in sorted(activity):
        if account_number not in tb_accounts:
            findings.append(
                IntegrityFinding(
                    TB_ROLLFORWARD,
                    f"Account has GL activity of {activity[account_number]:,.2f} but is not in the trial balance.",
                    account_number=account_number,
                )
            )
    return findings
