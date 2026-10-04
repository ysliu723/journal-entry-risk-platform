from datetime import date, datetime
from decimal import Decimal

import pytest

from app.config import DEFAULT_WEIGHTS
from app.rules import default_rules
from app.rules.amount import NearApprovalThresholdRule, RoundAmountRule
from app.rules.controls import MissingApprovalRule, SelfApprovalRule
from app.rules.description import VagueDescriptionRule
from app.rules.population import InfrequentPreparerRule, SeldomUsedAccountRule
from app.rules.timing import LateNightEntryRule, PeriodEndEntryRule, PostCloseEntryRule, WeekendEntryRule
from tests.factories import entry_with_amount, line, make_config, make_context, make_entry


def fires(rule, entry, context=None) -> bool:
    return rule.evaluate(entry, context or make_context([entry])) is not None


def test_default_rules_match_the_weight_table():
    assert sorted(rule.code for rule in default_rules()) == sorted(DEFAULT_WEIGHTS)


def test_which_rules_apply_to_manual_entries_only():
    manual_only = {rule.code for rule in default_rules() if rule.manual_only}
    assert manual_only == {
        "WEEKEND_ENTRY",
        "LATE_NIGHT_ENTRY",
        "PERIOD_END_ENTRY",
        "ROUND_AMOUNT",
        "NEAR_APPROVAL_THRESHOLD",
        "MISSING_APPROVAL",
        "VAGUE_DESCRIPTION",
    }


class TestWeekendEntry:
    @pytest.mark.parametrize(
        "created_at, expected",
        [
            (datetime(2026, 11, 14, 11, 0), True),  # Saturday
            (datetime(2026, 11, 15, 11, 0), True),  # Sunday
            (datetime(2026, 11, 16, 11, 0), False),  # Monday
        ],
    )
    def test_weekend(self, created_at, expected):
        assert fires(WeekendEntryRule(), make_entry(created_at=created_at)) == expected

    def test_hit_carries_code_weight_version_and_reason(self):
        hit = WeekendEntryRule().evaluate(make_entry(created_at=datetime(2026, 11, 14, 11, 0)), make_context())
        assert (hit.rule_code, hit.score, hit.rule_version) == ("WEEKEND_ENTRY", 10, 1)
        assert hit.reason == "Entry was created on a Saturday."


class TestLateNightEntry:
    @pytest.mark.parametrize("hour, minute, expected", [(21, 59, False), (22, 0, True), (5, 59, True), (6, 0, False)])
    def test_boundaries(self, hour, minute, expected):
        entry = make_entry(created_at=datetime(2026, 11, 18, hour, minute))
        assert fires(LateNightEntryRule(), entry) == expected


class TestPeriodEndEntry:
    @pytest.mark.parametrize(
        "posting_date, expected",
        [(date(2026, 12, 28), False), (date(2026, 12, 29), True), (date(2026, 12, 31), True), (date(2027, 1, 1), False)],
    )
    def test_last_three_days_of_the_fiscal_year(self, posting_date, expected):
        assert fires(PeriodEndEntryRule(), make_entry(posting_date=posting_date)) == expected

    def test_uses_the_clients_own_fiscal_year_end(self):
        config = make_config(fiscal_year_end=date(2026, 6, 30), period_close_date=date(2026, 7, 15))
        june = make_entry(posting_date=date(2026, 6, 29))
        december = make_entry(posting_date=date(2026, 12, 31))
        assert fires(PeriodEndEntryRule(), june, make_context([june], config))
        assert not fires(PeriodEndEntryRule(), december, make_context([december], config))


class TestPostCloseEntry:
    def test_created_after_close_but_dated_in_the_closed_year(self):
        entry = make_entry(posting_date=date(2026, 12, 30), created_at=datetime(2027, 1, 12, 18, 20))
        hit = PostCloseEntryRule().evaluate(entry, make_context())
        assert hit.reason == (
            "Entry was created on 2027-01-12, after the books closed on 2027-01-10, but posted to 2026-12-30."
        )

    def test_created_on_the_close_date_is_fine(self):
        entry = make_entry(posting_date=date(2026, 12, 31), created_at=datetime(2027, 1, 10, 17, 0))
        assert not fires(PostCloseEntryRule(), entry)

    def test_entry_dated_in_the_next_year_is_fine(self):
        entry = make_entry(posting_date=date(2027, 1, 12), created_at=datetime(2027, 1, 12, 10, 0))
        assert not fires(PostCloseEntryRule(), entry)


class TestRoundAmount:
    @pytest.mark.parametrize(
        "amount, expected",
        [("30000.00", True), ("10000.00", True), ("150000.00", True), ("9000.00", False), ("30500.00", False)],
    )
    def test_round_amounts_above_the_minimum(self, amount, expected):
        assert fires(RoundAmountRule(), entry_with_amount(amount)) == expected

    def test_round_unit_is_configurable(self):
        entry = entry_with_amount("30500.00")
        assert fires(RoundAmountRule(), entry, make_context([entry], make_config(round_amount_unit=Decimal("500"))))


class TestNearApprovalThreshold:
    @pytest.mark.parametrize(
        "amount, expected",
        [("94999.99", False), ("95000.00", True), ("99999.99", True), ("100000.00", False)],
    )
    def test_within_five_percent_below_the_threshold(self, amount, expected):
        assert fires(NearApprovalThresholdRule(), entry_with_amount(amount)) == expected

    def test_reason(self):
        hit = NearApprovalThresholdRule().evaluate(entry_with_amount("99800.00"), make_context())
        assert hit.reason == "Amount 99,800.00 is within 5% below the 100,000.00 approval threshold."

    def test_threshold_is_configurable(self):
        config = make_config(approval_threshold=Decimal("250000"))
        assert fires(NearApprovalThresholdRule(), entry_with_amount("240000.00"), make_context(config=config))
        assert not fires(NearApprovalThresholdRule(), entry_with_amount("99800.00"), make_context(config=config))


class TestMissingApproval:
    @pytest.mark.parametrize(
        "amount, approved_by, expected",
        [("50000.00", None, False), ("50000.01", None, True), ("80000.00", "gl_manager", False)],
    )
    def test_large_entries_need_an_approver(self, amount, approved_by, expected):
        assert fires(MissingApprovalRule(), entry_with_amount(amount, approved_by=approved_by)) == expected


class TestSelfApproval:
    def test_same_user_prepared_and_approved(self):
        hit = SelfApprovalRule().evaluate(make_entry(prepared_by="gl_acct1", approved_by="gl_acct1"), make_context())
        assert (hit.rule_code, hit.score) == ("SELF_APPROVAL", 40)
        assert "gl_acct1" in hit.reason

    def test_comparison_ignores_case_and_spaces(self):
        assert fires(SelfApprovalRule(), make_entry(prepared_by="gl_acct1", approved_by=" GL_ACCT1 "))

    def test_different_users(self):
        assert not fires(SelfApprovalRule(), make_entry(prepared_by="gl_acct1", approved_by="gl_manager"))

    def test_no_approver_is_left_to_missing_approval(self):
        assert not fires(SelfApprovalRule(), make_entry(approved_by=None))


class TestVagueDescription:
    @pytest.mark.parametrize(
        "description, expected",
        [
            ("", True),
            ("   ", True),
            ("Year-end adjustment", True),
            ("Misc expense", True),
            ("Revenue recognition - per CFO", True),
            ("Other expenses", True),
            ("Mother's Day gift cards", False),
            ("Payroll December 2026", False),
        ],
    )
    def test_generic_descriptions(self, description, expected):
        assert fires(VagueDescriptionRule(), make_entry(description=description)) == expected

    def test_keywords_are_configurable(self):
        config = make_config(vague_description_keywords=("tbd",))
        assert fires(VagueDescriptionRule(), make_entry(description="Accrual TBD"), make_context(config=config))
        assert not fires(VagueDescriptionRule(), make_entry(description="Year-end adjustment"), make_context(config=config))


def five_common_entries_and_one_unusual(**unusual_fields):
    """Five ordinary entries by gl_acct1 on 610100/200100, plus one entry with the given changes."""
    common = [entry_with_amount("100.00", entry_id=f"JE{n:06d}") for n in range(1, 6)]
    unusual = make_entry(entry_id="JE000006", **unusual_fields)
    return common, unusual


class TestSeldomUsedAccount:
    def test_flags_an_account_used_by_fewer_entries_than_the_minimum(self):
        common, unusual = five_common_entries_and_one_unusual(
            lines=(line(1, "690900", debit="50.00"), line(2, "200100", credit="50.00"))
        )
        context = make_context(common + [unusual])  # minimum is 5 by default

        hit = SeldomUsedAccountRule().evaluate(unusual, context)

        assert hit.reason == "Uses seldom-used account(s): 690900 (used by 1 of 6 entries)."
        assert not fires(SeldomUsedAccountRule(), common[0], context)  # 610100 is used exactly 5 times

    def test_minimum_is_configurable(self):
        common, unusual = five_common_entries_and_one_unusual(
            lines=(line(1, "690900", debit="50.00"), line(2, "200100", credit="50.00"))
        )
        context = make_context(common + [unusual], make_config(min_account_usage=1))
        assert not fires(SeldomUsedAccountRule(), unusual, context)


class TestInfrequentPreparer:
    def test_flags_someone_who_rarely_prepares_entries(self):
        common, unusual = five_common_entries_and_one_unusual(prepared_by="controller01")
        context = make_context(common + [unusual])

        hit = InfrequentPreparerRule().evaluate(unusual, context)

        assert hit.reason == "Preparer controller01 prepared only 1 of 6 entries."
        assert not fires(InfrequentPreparerRule(), common[0], context)  # gl_acct1 prepared exactly 5
