"""Trial balance: one row per account with its opening and closing balance.

Balances are debit-positive: balance = debits - credits. Asset and expense
accounts are normally positive; liability, equity, and revenue accounts
are normally negative.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


@dataclass(frozen=True)
class TrialBalanceLine:
    account_number: str
    account_name: str
    account_type: AccountType
    opening_balance: Decimal
    closing_balance: Decimal
