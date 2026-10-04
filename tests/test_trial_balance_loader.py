from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.trial_balance import AccountType
from app.ingestion.csv_loader import load_trial_balance

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
HEADER = "account_number,account_name,account_type,opening_balance,closing_balance\n"


def write_tb(tmp_path, body: str) -> Path:
    path = tmp_path / "tb.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_loads_sample_trial_balance():
    trial_balance = load_trial_balance(SAMPLE_DIR / "trial_balance.csv")
    assert len(trial_balance) == 13
    cash = trial_balance[0]
    assert cash.account_number == "100100"
    assert cash.account_type is AccountType.ASSET
    assert cash.opening_balance == Decimal("1200000.00")
    # A trial balance balances: debit-positive balances sum to zero.
    assert sum(tb.opening_balance for tb in trial_balance) == 0
    assert sum(tb.closing_balance for tb in trial_balance) == 0


def test_any_invalid_row_fails_the_whole_file(tmp_path):
    path = write_tb(tmp_path, "100100,Cash,ASSET,1000.00,1200.00\n200100,Payables,LIABILITY,abc,-50.00\n")
    with pytest.raises(ValueError, match="line 3: opening_balance is not a valid amount"):
        load_trial_balance(path)


def test_rejects_unknown_account_type(tmp_path):
    path = write_tb(tmp_path, "100100,Cash,CASH,1000.00,1200.00\n")
    with pytest.raises(ValueError, match="account_type must be one of"):
        load_trial_balance(path)


def test_rejects_an_account_listed_twice(tmp_path):
    path = write_tb(tmp_path, "100100,Cash,ASSET,1000.00,1200.00\n100100,Cash again,ASSET,0.00,0.00\n")
    with pytest.raises(ValueError, match="account 100100 appears twice"):
        load_trial_balance(path)
