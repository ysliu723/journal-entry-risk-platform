"""The shapes of the JSON the API returns (Pydantic models).

Amounts are Decimal. Pydantic writes them as JSON strings ("99800.00"), so
no client ever receives a rounded floating-point number.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    # Lets a response be built straight from a database record's attributes.
    model_config = ConfigDict(from_attributes=True)


class LoadErrorOut(ApiModel):
    row_number: int
    entry_id: str
    message: str


class IntegrityFindingOut(ApiModel):
    check_code: str
    entry_id: str | None
    account_number: str | None
    message: str


class RunSummary(ApiModel):
    id: int
    created_at: datetime
    gl_filename: str
    trial_balance_filename: str
    rule_set_version: str
    entry_count: int
    rejected_row_count: int
    integrity_passed: bool
    level_counts: dict[str, int]


class RunDetail(RunSummary):
    config: dict
    load_errors: list[LoadErrorOut]
    integrity_findings: list[IntegrityFindingOut]


class RuleHitOut(ApiModel):
    rule_code: str
    rule_version: int
    score: int
    reason: str


class EntryLineOut(ApiModel):
    line_number: int
    account_number: str
    debit: Decimal
    credit: Decimal
    department: str | None
    vendor: str | None


class EntrySummary(ApiModel):
    id: int
    run_id: int
    entry_id: str
    posting_date: date
    created_at: datetime
    source: str
    description: str
    prepared_by: str
    approved_by: str | None
    amount: Decimal
    risk_score: int
    risk_level: str
    rule_codes: list[str]


class EntryDetail(EntrySummary):
    currency: str
    lines: list[EntryLineOut]
    hits: list[RuleHitOut]


class EntryPage(BaseModel):
    items: list[EntrySummary]
    total: int
    page: int
    page_size: int
