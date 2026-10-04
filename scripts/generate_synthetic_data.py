"""Generate a synthetic but coherent general ledger for fiscal year 2026.

    python -m scripts.generate_synthetic_data --entries 100000 --seed 42 --defects

Writes to data/generated/ (ignored by git):
    gl_detail.csv            the GL export, one row per entry line
    trial_balance.csv        opening balances, and closing balances computed from the GL
    client_config.json       the client config for this population
    ground_truth.csv         which entries were injected as risk scenarios (the engine never reads it)
    gl_detail_defective.csv  with --defects: the same GL with data problems for the integrity checks
    defects.json             with --defects: which entries were damaged, and how

The company has the usual daily system postings (AP, AR, bank, payroll,
depreciation), recurring templates, and manual entries by the accounting
team. About 0.2% of entries are injected risk scenarios. One scenario
(FICTITIOUS_VENDOR) is deliberately invisible to the current rules, to show
what rules alone cannot catch.
"""

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from app.ingestion.csv_loader import GL_DETAIL_COLUMNS, TRIAL_BALANCE_COLUMNS

YEAR_START = date(2026, 1, 1)
FISCAL_YEAR_END = date(2026, 12, 31)
PERIOD_CLOSE_DATE = date(2027, 1, 10)
LAST_NORMAL_CLOSE_DAY = date(2027, 1, 8)  # normal year-end entries are keyed in before the close

# number, name, type, opening balance (debit-positive). Retained earnings is computed so the TB balances.
ACCOUNTS = [
    ("100100", "Cash - Operating", "ASSET", "2500000.00"),
    ("100200", "Cash - Payroll", "ASSET", "300000.00"),
    ("110100", "Accounts Receivable", "ASSET", "1800000.00"),
    ("120100", "Inventory", "ASSET", "900000.00"),
    ("130100", "Prepaid Expenses", "ASSET", "150000.00"),
    ("150100", "Fixed Assets", "ASSET", "4000000.00"),
    ("150900", "Accumulated Depreciation", "ASSET", "-1200000.00"),
    ("200100", "Accounts Payable", "LIABILITY", "-1100000.00"),
    ("210100", "Accrued Liabilities", "LIABILITY", "-400000.00"),
    ("220100", "Payroll Liabilities", "LIABILITY", "-250000.00"),
    ("230100", "Deferred Revenue", "LIABILITY", "-300000.00"),
    ("240100", "Income Tax Payable", "LIABILITY", "-150000.00"),
    ("300100", "Common Stock", "EQUITY", "-1000000.00"),
    ("310100", "Retained Earnings", "EQUITY", None),
    ("400100", "Product Revenue", "REVENUE", "0.00"),
    ("410100", "Service Revenue", "REVENUE", "0.00"),
    ("490100", "Other Income", "REVENUE", "0.00"),
    ("500100", "Cost of Goods Sold", "EXPENSE", "0.00"),
    ("610100", "Consulting Expense", "EXPENSE", "0.00"),
    ("611100", "Legal Fees", "EXPENSE", "0.00"),
    ("620100", "Salaries Expense", "EXPENSE", "0.00"),
    ("621100", "Payroll Tax Expense", "EXPENSE", "0.00"),
    ("630100", "Depreciation Expense", "EXPENSE", "0.00"),
    ("640100", "Rent Expense", "EXPENSE", "0.00"),
    ("641100", "Utilities", "EXPENSE", "0.00"),
    ("650100", "Software Subscriptions", "EXPENSE", "0.00"),
    ("660100", "Travel and Entertainment", "EXPENSE", "0.00"),
    ("670100", "Marketing", "EXPENSE", "0.00"),
    ("680100", "Insurance Expense", "EXPENSE", "0.00"),
    ("690100", "Bank Fees", "EXPENSE", "0.00"),
    ("690500", "Income Tax Expense", "EXPENSE", "0.00"),
    ("690900", "Miscellaneous Expense", "EXPENSE", "0.00"),
    ("699900", "Suspense Account", "EXPENSE", "0.00"),
]

# What AP invoices are booked to, and how often.
AP_EXPENSE_WEIGHTS = {
    "500100": 25,  # purchases for resale
    "130100": 2,  # prepaid contracts
    "610100": 10,
    "611100": 4,
    "641100": 8,
    "650100": 8,
    "660100": 15,
    "670100": 10,
    "690100": 3,
}
DEPARTMENT_BY_ACCOUNT = {
    "120100": "Operations",
    "400100": "Sales",
    "410100": "Sales",
    "500100": "Operations",
    "610100": "Finance",
    "611100": "Legal",
    "620100": "HR",
    "621100": "HR",
    "641100": "Operations",
    "650100": "IT",
    "660100": "Sales",
    "670100": "Marketing",
}
VENDORS = [
    "Northwind Advisory", "Fabrikam Inc", "Adventure Works", "Tailspin Toys", "Wide World Importers",
    "Litware", "Proseware", "Alpine Ski House", "Coho Winery", "Fourth Coffee", "Humongous Insurance",
    "Lucerne Publishing", "Margie's Travel", "Trey Research", "Wingtip Toys", "Blue Yonder Airlines",
    "City Power and Light", "Consolidated Messenger", "Southridge Video", "Tasmanian Traders",
    "A. Datum Corporation", "Relecloud", "Graphic Design Institute", "VanArsdel",
]
CUSTOMERS = [
    "Contoso Ltd", "Woodgrove Bank", "Bellows College", "Best For You Organics", "School of Fine Art",
    "The Phone Company", "Trey Research", "Coho Vineyard", "Lamna Healthcare", "Munson's Pickles",
]
GL_ACCOUNTANTS = {"gl_acct1": 40, "gl_acct2": 25, "gl_acct3": 20, "gl_acct4": 15}

INJECTED_SHARE = 0.002
SCENARIO_SHARES = {
    "YEAR_END_REVENUE_TOPSIDE": 0.30,
    "THRESHOLD_AVOIDANCE": 0.25,
    "SELF_APPROVED_PAYMENT": 0.15,
    "SUSPENSE_ACCOUNT": 0.10,
    "FICTITIOUS_VENDOR": 0.20,
}


@dataclass
class Line:
    account: str
    debit: Decimal
    credit: Decimal
    vendor: str = ""


@dataclass
class Entry:
    posting_date: date
    created_at: datetime
    source: str
    description: str
    prepared_by: str
    approved_by: str
    lines: list[Line]
    scenario: str = ""  # set only for injected risk scenarios
    entry_id: str = ""


def pair(debit_account: str, credit_account: str, amount: Decimal, vendor: str = "") -> list[Line]:
    """The simplest entry: one debit line and one credit line for the same amount."""
    return [Line(debit_account, amount, Decimal("0")), Line(credit_account, Decimal("0"), amount, vendor)]


def cents(value: int) -> Decimal:
    return Decimal(value).scaleb(-2)  # 123456 -> Decimal("1234.56")


class LedgerGenerator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.days = [YEAR_START + timedelta(days=n) for n in range(365)]
        self.weekdays = [day for day in self.days if day.weekday() < 5]
        self.close_days = set()  # the last three business days of each month
        self.month_starts = []  # the first business day of each month
        for month in range(1, 13):
            business_days = [day for day in self.weekdays if day.month == month]
            self.close_days.update(business_days[-3:])
            self.month_starts.append(business_days[0])

    # ---- random building blocks ----

    def amount(self, median: float, spread: float, maximum: float = 2_000_000) -> Decimal:
        """A log-normal amount: most are near the median, a few are much larger."""
        value = min(self.rng.lognormvariate(math.log(median), spread), maximum)
        return cents(max(int(round(value * 100)), 1000))

    def round_amount(self, low: int, high: int, unit: int) -> Decimal:
        return Decimal(self.rng.randrange(low, high + 1, unit)).quantize(Decimal("0.01"))

    def at(self, day: date, first_hour: int, last_hour: int) -> datetime:
        hour = self.rng.randint(first_hour, last_hour)
        return datetime.combine(day, time(hour, self.rng.randint(0, 59), self.rng.randint(0, 59)))

    def weighted(self, weights: dict[str, int]) -> str:
        return self.rng.choices(list(weights), weights=list(weights.values()))[0]

    def manual_created_at(self, posting_date: date) -> datetime:
        """When a person keys in an entry: usually business hours, often a few days late during the close."""
        lag = 0
        if posting_date in self.close_days:
            lag = self.rng.choice([0, 0, 1, 2, 3, 4, 5])
        elif self.rng.random() < 0.2:
            lag = self.rng.choice([1, 2])
        created = posting_date + timedelta(days=lag)
        if created.weekday() >= 5 and self.rng.random() < 0.8:
            created += timedelta(days=7 - created.weekday())  # most people do not work weekends
        if posting_date <= FISCAL_YEAR_END:
            created = min(created, LAST_NORMAL_CLOSE_DAY)
        if self.rng.random() < 0.01:
            return self.at(created, 22, 23)  # the occasional late night
        return self.at(created, 8, 18)

    # ---- the normal population ----

    def normal_entries(self, count: int) -> list[Entry]:
        entries = self.payroll() + self.depreciation() + self.recurring() + self.tax_provisions()
        remaining = count - len(entries)
        makers = [(self.ap_invoice, 0.27), (self.ap_payment, 0.27), (self.ar_invoice, 0.18), (self.ar_receipt, 0.18)]
        for maker, share in makers:
            for _ in range(int(remaining * share)):
                entries.append(maker())
        entries += self.manual_entries(count - len(entries))  # about 10%; fills the total exactly
        return entries

    def ap_invoice(self) -> Entry:
        day = self.rng.choice(self.weekdays)
        vendor = self.rng.choice(VENDORS)
        account = self.weighted(AP_EXPENSE_WEIGHTS)
        amount = self.amount(2000, 1.2, 400_000)
        return Entry(
            day, self.at(day, 1, 4), "SYSTEM", f"AP invoice {self.rng.randint(10000, 99999)} - {vendor}",
            "SYS_AP", "", pair(account, "200100", amount, vendor),
        )

    def ap_payment(self) -> Entry:
        day = self.rng.choice(self.days)  # the bank batch also runs on weekends
        vendor = self.rng.choice(VENDORS)
        amount = self.amount(1900, 1.2, 400_000)  # a little less than invoiced, so payables grow slowly
        return Entry(
            day, self.at(day, 3, 5), "SYSTEM", f"Payment {self.rng.randint(100000, 999999)} - {vendor}",
            "SYS_BANK", "", pair("200100", "100100", amount, vendor),
        )

    def ar_invoice(self) -> Entry:
        day = self.rng.choice(self.weekdays)
        customer = self.rng.choice(CUSTOMERS)
        total = self.amount(4000, 1.1, 600_000)
        description = f"Invoice INV-{self.rng.randint(100000, 999999)} - {customer}"
        if self.rng.random() < 0.25:  # product and service on one invoice
            product = cents(int(total * 100 * Decimal("0.7")))
            lines = [
                Line("110100", total, Decimal("0")),
                Line("400100", Decimal("0"), product),
                Line("410100", Decimal("0"), total - product),
            ]
        else:
            lines = pair("110100", self.rng.choice(["400100", "400100", "410100"]), total)
        return Entry(day, self.at(day, 1, 4), "SYSTEM", description, "SYS_AR", "", lines)

    def ar_receipt(self) -> Entry:
        day = self.rng.choice(self.days)
        customer = self.rng.choice(CUSTOMERS)
        amount = self.amount(3800, 1.1, 600_000)  # a little less than invoiced, so receivables grow slowly
        return Entry(
            day, self.at(day, 3, 5), "SYSTEM", f"Receipt {self.rng.randint(100000, 999999)} - {customer}",
            "SYS_BANK", "", pair("100100", "110100", amount),
        )

    def payroll(self) -> list[Entry]:
        entries = []
        for month in range(1, 13):
            month_days = [day for day in self.days if day.month == month]
            for pay_day in (month_days[14], month_days[-1]):
                gross = cents(self.rng.randint(37_000_000, 39_000_000))
                withheld = cents(int(gross * 100 * Decimal("0.25")))
                employer_tax = cents(int(gross * 100 * Decimal("0.0765")))
                lines = [
                    Line("620100", gross, Decimal("0")),
                    Line("621100", employer_tax, Decimal("0")),
                    Line("100200", Decimal("0"), gross - withheld),
                    Line("220100", Decimal("0"), withheld + employer_tax),
                ]
                net_pay = gross - withheld
                entries.append(
                    Entry(pay_day, self.at(pay_day, 1, 2), "SYSTEM", f"Payroll {pay_day:%B %d, %Y}", "SYS_PAYROLL", "", lines)
                )
                entries.append(
                    Entry(pay_day, self.at(pay_day, 0, 0), "SYSTEM", f"Fund payroll account {pay_day:%B %d, %Y}",
                          "SYS_BANK", "", pair("100200", "100100", net_pay))
                )
                entries.append(
                    Entry(pay_day, self.at(pay_day, 5, 5), "SYSTEM", f"Payroll tax remittance {pay_day:%B %d, %Y}",
                          "SYS_BANK", "", pair("220100", "100100", withheld + employer_tax))
                )
        return entries

    def depreciation(self) -> list[Entry]:
        entries = []
        for month in range(1, 13):
            day = max(d for d in self.days if d.month == month)
            entries.append(
                Entry(day, self.at(day, 2, 3), "SYSTEM", f"Depreciation {day:%B %Y}", "SYS_FA", "",
                      pair("630100", "150900", Decimal("33333.33")))
            )
        return entries

    def recurring(self) -> list[Entry]:
        """Monthly templates set up once by gl_acct1 and posted by the scheduler just after midnight."""
        templates = [
            ("640100", "100100", Decimal("45000.00"), "Office rent", ""),
            ("680100", "130100", Decimal("12500.00"), "Insurance amortization", ""),
            ("650100", "200100", Decimal("18750.00"), "ERP subscription", "Relecloud"),
        ]
        entries = []
        for day in [d for d in self.days if d.day == 1]:
            for debit, credit, amount, name, vendor in templates:
                entries.append(
                    Entry(day, datetime.combine(day, time(0, 30)), "RECURRING", f"{name} {day:%B %Y}",
                          "gl_acct1", "gl_manager", pair(debit, credit, amount, vendor))
                )
        return entries

    def tax_provisions(self) -> list[Entry]:
        """Quarterly tax provisions: legitimate entries by a rare preparer, with round estimates."""
        entries = []
        for quarter_end in (date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)):
            created = min(quarter_end + timedelta(days=4), LAST_NORMAL_CLOSE_DAY)
            amount = self.round_amount(60_000, 140_000, 5_000)
            entries.append(
                Entry(quarter_end, self.at(created, 9, 17), "MANUAL",
                      f"Income tax provision Q{(quarter_end.month - 1) // 3 + 1} 2026",
                      "controller01", "cfo", pair("690500", "240100", amount))
            )
        return entries

    def manual_entries(self, count: int) -> list[Entry]:
        entries = []
        while len(entries) < count:
            entries += self.manual_entry()
        return entries[:count]

    def manual_entry(self) -> list[Entry]:
        """One manual entry; an accrual comes with its reversal on the first business day of the next month."""
        kind = self.rng.choices(
            ["accrual", "reclass", "inventory", "prepaid", "credit_memo", "misc"],
            weights=[45, 20, 7, 7, 20, 1],
        )[0]
        vendor = self.rng.choice(VENDORS)
        amount = self.amount(6000, 1.1, 400_000)
        preparer = self.weighted(GL_ACCOUNTANTS)
        expense = self.rng.choice(["610100", "611100", "641100", "650100", "660100", "670100"])

        if kind == "accrual":
            day = self.rng.choice(sorted(self.close_days))
            if amount >= 10_000 and self.rng.random() < 0.15:
                amount = (amount / 1000).to_integral_value() * 1000  # an estimate, so a round number
                amount = amount.quantize(Decimal("0.01"))
            description = f"{day:%B} accrual - {vendor}"
            lines = pair(expense, "210100", amount, vendor)
        elif kind == "reclass":
            day = self.rng.choice(self.weekdays)
            other = self.rng.choice([a for a in ["610100", "611100", "650100", "670100"] if a != expense])
            description = f"Reclass {vendor} invoice to correct cost center"
            lines = pair(expense, other, amount)
        elif kind == "inventory":
            day = self.rng.choice(sorted(self.close_days))
            description = f"Inventory count adjustment - warehouse {self.rng.randint(1, 4)}"
            amount = self.amount(1500, 0.8, 50_000)
            if self.rng.random() < 0.5:
                lines = pair("500100", "120100", amount)  # shrinkage
            else:
                lines = pair("120100", "500100", amount)  # count found more than the books
        elif kind == "prepaid":
            day = self.rng.choice(sorted(self.close_days))
            description = f"Amortize prepaid {self.rng.choice(['software', 'insurance', 'maintenance'])} contract"
            amount = self.amount(1200, 0.6, 20_000)
            lines = pair(self.rng.choice(["650100", "680100"]), "130100", amount)
        elif kind == "credit_memo":
            day = self.rng.choice(self.weekdays)
            preparer = self.rng.choice(["ap_clerk1", "ap_clerk2", "ap_clerk3"])
            description = f"Credit memo CM-{self.rng.randint(1000, 9999)} - {vendor}"
            amount = self.amount(800, 0.8, 20_000)
            lines = pair("200100", expense, amount, vendor)
        else:  # the rare, legitimate miscellaneous expense
            day = self.rng.choice(self.weekdays)
            amount = self.amount(800, 0.6, 5_000)
            description = f"Office supplies - {vendor}"
            lines = pair("690900", "100100", amount, vendor)

        approver = "ap_manager" if preparer.startswith("ap_") else "gl_manager"
        if amount >= 100_000:
            approver = "controller01"  # second-level approval above the threshold
        elif self.rng.random() < 0.03:
            approver = ""  # small entries sometimes skip review
        entry = Entry(day, self.manual_created_at(day), "MANUAL", description, preparer, approver, lines)
        if kind != "accrual" or day.month == 12:
            return [entry]  # December accruals stay open at year end
        reversal_day = self.month_starts[day.month]  # first business day of the next month
        reversal = Entry(
            reversal_day, self.manual_created_at(reversal_day), "MANUAL", f"Reversal of {day:%B} accrual - {vendor}",
            preparer, approver, pair("210100", expense, amount, vendor),
        )
        return [entry, reversal]

    # ---- injected risk scenarios ----

    def injected_entries(self, count: int) -> list[Entry]:
        makers = {
            "YEAR_END_REVENUE_TOPSIDE": self.year_end_revenue_topside,
            "THRESHOLD_AVOIDANCE": self.threshold_avoidance,
            "SELF_APPROVED_PAYMENT": self.self_approved_payment,
            "SUSPENSE_ACCOUNT": self.suspense_account,
            "FICTITIOUS_VENDOR": self.fictitious_vendor,
        }
        entries = []
        for scenario, share in SCENARIO_SHARES.items():
            for _ in range(max(1, round(count * share))):
                entry = makers[scenario]()
                entry.scenario = scenario
                entries.append(entry)
        return entries

    def year_end_revenue_topside(self) -> Entry:
        """Manual revenue booked at year end by senior people to hit a target."""
        day = date(2026, 12, self.rng.choices([27, 28, 29, 30, 31], weights=[1, 1, 2, 3, 3])[0])
        roll = self.rng.random()
        if roll < 0.45:
            created = self.at(day, 22, 23)  # late at night
        elif roll < 0.75:
            created = self.at(date(2027, 1, self.rng.randint(11, 25)), 9, 18)  # after the close
        else:
            created = self.at(day, 9, 18)
        if self.rng.random() < 0.7:
            amount = self.round_amount(50_000, 500_000, 5_000)
        else:
            amount = self.amount(180_000, 0.4, 600_000)
        preparer = self.rng.choices(["controller01", "cfo", "gl_acct2"], weights=[40, 25, 35])[0]
        roll = self.rng.random()
        if roll < 0.4:
            approver = ""
        elif roll < 0.6:
            approver = preparer.upper()
        else:
            approver = "cfo" if preparer != "cfo" else "controller01"
        description = self.rng.choice([
            "Revenue adjustment",
            "Q4 revenue true-up per CFO",
            "Top-side revenue entry",
            "Revenue recognition - per CFO",
            "Year-end sales cut-off adjustment",
            "Customer settlement - Contoso Ltd",
        ])
        return Entry(day, created, "MANUAL", description, preparer, approver, pair("110100", "400100", amount))

    def threshold_avoidance(self) -> Entry:
        """Invoices kept just under the 100,000 approval threshold."""
        day = self.rng.choice(self.weekdays)
        vendor = self.rng.choice(["Trey Research", "Litware", "Proseware"])
        amount = cents(self.rng.randint(9_500_000, 9_995_000))
        approver = "" if self.rng.random() < 0.5 else "gl_manager"
        return Entry(
            day, self.at(day, 9, 17), "MANUAL", f"Consulting services - {vendor} - phase {self.rng.randint(1, 6)}",
            self.rng.choice(["gl_acct3", "ap_clerk1"]), approver,
            pair(self.rng.choice(["610100", "611100"]), "200100", amount, vendor),
        )

    def self_approved_payment(self) -> Entry:
        """A clerk pays a vendor and approves the payment personally."""
        day = self.rng.choice(self.weekdays)
        vendor = self.rng.choice(VENDORS)
        approver = self.rng.choice(["ap_clerk2", "AP_CLERK2", " ap_clerk2"])
        return Entry(
            day, self.at(day, 9, 17), "MANUAL", f"Manual payment - {vendor} - urgent",
            "ap_clerk2", approver, pair("200100", "100100", self.amount(18_000, 0.5, 49_000), vendor),
        )

    def suspense_account(self) -> Entry:
        """Cash moved to a suspense account by a forgotten temporary user."""
        day = self.rng.choice(self.days)
        if self.rng.random() < 0.5:
            day += timedelta(days=5 - day.weekday()) if day.weekday() < 5 else timedelta(0)  # a Saturday
            day = min(day, FISCAL_YEAR_END)
        description = self.rng.choice(["misc", "plug", "to fix later", "suspense - to be cleared", ""])
        approver = "" if self.rng.random() < 0.7 else "gl_manager"
        return Entry(
            day, self.at(day, 10, 20), "MANUAL", description, "temp_user7", approver,
            pair("699900", "100100", self.amount(7500, 0.6, 40_000)),
        )

    def fictitious_vendor(self) -> Entry:
        """Ordinary-looking invoices from a look-alike vendor. No current rule can see this."""
        day = self.rng.choice(self.weekdays)
        quarter = (day.month - 1) // 3 + 1
        return Entry(
            day, self.at(day, 9, 17), "MANUAL", f"Consulting fee - Q{quarter} advisory services",
            "gl_acct4", "gl_manager",
            pair("610100", "200100", self.amount(14_000, 0.3, 45_000), "Northwind Advisory LLC"),
        )


def generate(entry_count: int, seed: int) -> list[Entry]:
    generator = LedgerGenerator(seed)
    injected_count = max(5, round(entry_count * INJECTED_SHARE))
    entries = generator.injected_entries(injected_count)
    entries += generator.normal_entries(entry_count - len(entries))
    # Entry numbers are handed out in the order entries are keyed in, like a real ERP.
    entries.sort(key=lambda entry: (entry.created_at, entry.posting_date, entry.description))
    width = max(6, len(str(len(entries))))
    for number, entry in enumerate(entries, start=1):
        entry.entry_id = f"JE{number:0{width}d}"
    return entries


def gl_rows(entries: list[Entry]) -> list[dict[str, str]]:
    rows = []
    for entry in entries:
        for line_number, line in enumerate(entry.lines, start=1):
            rows.append({
                "entry_id": entry.entry_id,
                "line_number": str(line_number),
                "posting_date": entry.posting_date.isoformat(),
                "created_at": entry.created_at.isoformat(sep=" "),
                "source": entry.source,
                "description": entry.description,
                "prepared_by": entry.prepared_by,
                "approved_by": entry.approved_by,
                "currency": "USD",
                "account_number": line.account,
                "debit": f"{line.debit:.2f}" if line.debit else "",
                "credit": f"{line.credit:.2f}" if line.credit else "",
                "department": DEPARTMENT_BY_ACCOUNT.get(line.account, "Finance"),
                "vendor": line.vendor,
            })
    return rows


def trial_balance_rows(entries: list[Entry]) -> list[dict[str, str]]:
    activity = {number: Decimal("0") for number, _, _, _ in ACCOUNTS}
    for entry in entries:
        for line in entry.lines:
            activity[line.account] += line.debit - line.credit

    known = sum(Decimal(opening) for _, _, _, opening in ACCOUNTS if opening is not None)
    rows = []
    for number, name, account_type, opening in ACCOUNTS:
        opening_balance = Decimal(opening) if opening is not None else -known  # retained earnings balances the TB
        rows.append({
            "account_number": number,
            "account_name": name,
            "account_type": account_type,
            "opening_balance": f"{opening_balance:.2f}",
            "closing_balance": f"{opening_balance + activity[number]:.2f}",
        })
    return rows


def damage(rows: list[dict[str, str]], entries: list[Entry], seed: int) -> tuple[list[dict[str, str]], dict]:
    """Copy the GL rows and introduce one of each data problem the integrity checks look for."""
    rng = random.Random(seed + 1)
    manual_ids = [entry.entry_id for entry in entries if entry.source == "MANUAL" and not entry.scenario]
    unbalanced, duplicated, deleted, malformed = rng.sample(manual_ids, 4)
    damaged = []
    for row in rows:
        row = dict(row)
        if row["entry_id"] == deleted:
            continue
        if row["entry_id"] == unbalanced and row["line_number"] == "1":
            row["debit"] = f"{Decimal(row['debit']) + Decimal('100.00'):.2f}"
        if row["entry_id"] == malformed and row["line_number"] == "1":
            row["posting_date"] = "2026-02-30"
        damaged.append(row)
    damaged += [dict(row) for row in rows if row["entry_id"] == duplicated]  # exported twice
    defects = {
        "UNBALANCED_ENTRY": unbalanced,
        "DUPLICATE_ENTRY_ID": duplicated,
        "DELETED_ENTRY": deleted,
        "MALFORMED_ROW": malformed,
    }
    return damaged, defects


def write_csv(path: Path, columns, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--defects", action="store_true", help="also write gl_detail_defective.csv")
    args = parser.parse_args()

    entries = generate(args.entries, args.seed)
    rows = gl_rows(entries)
    args.out.mkdir(parents=True, exist_ok=True)

    write_csv(args.out / "gl_detail.csv", GL_DETAIL_COLUMNS, rows)
    write_csv(args.out / "trial_balance.csv", TRIAL_BALANCE_COLUMNS, trial_balance_rows(entries))
    write_csv(
        args.out / "ground_truth.csv",
        ["entry_id", "scenario"],
        [{"entry_id": e.entry_id, "scenario": e.scenario} for e in entries if e.scenario],
    )
    config = {
        "fiscal_year_end": FISCAL_YEAR_END.isoformat(),
        "period_close_date": PERIOD_CLOSE_DATE.isoformat(),
        # With 100,000 entries, "seldom" and "infrequent" mean fewer than 50, not fewer than 5.
        "min_account_usage": 50,
        "min_preparer_entries": 50,
    }
    (args.out / "client_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    injected = sum(1 for entry in entries if entry.scenario)
    print(f"Wrote {len(entries):,} entries ({len(rows):,} lines, {injected} injected) to {args.out}/")

    if args.defects:
        damaged, defects = damage(rows, entries, args.seed)
        write_csv(args.out / "gl_detail_defective.csv", GL_DETAIL_COLUMNS, damaged)
        (args.out / "defects.json").write_text(json.dumps(defects, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote gl_detail_defective.csv with: {defects}")


if __name__ == "__main__":
    main()
