from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import DEFAULT_WEIGHTS, ClientConfig, load_config
from tests.factories import make_config

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def test_defaults():
    config = make_config()
    assert config.approval_threshold == Decimal("100000")
    assert config.weight("MISSING_APPROVAL") == 40


def test_from_dict_parses_dates_amounts_and_weights():
    config = ClientConfig.from_dict(
        {
            "fiscal_year_end": "2026-06-30",
            "period_close_date": "2026-07-15",
            "approval_threshold": "250000",
            "near_threshold_ratio": 0.9,
            "weights": {"ROUND_AMOUNT": 5},
        }
    )
    assert config.fiscal_year_end == date(2026, 6, 30)
    assert config.approval_threshold == Decimal("250000")
    assert config.near_threshold_ratio == Decimal("0.9")
    assert config.weight("ROUND_AMOUNT") == 5
    assert config.weight("MISSING_APPROVAL") == 40  # weights not mentioned keep their defaults


def test_rejects_misspelled_keys():
    with pytest.raises(ValueError, match="unknown config keys: aproval_threshold"):
        ClientConfig.from_dict(
            {"fiscal_year_end": "2026-12-31", "period_close_date": "2027-01-10", "aproval_threshold": "1"}
        )


def test_rejects_unknown_rule_in_weights():
    with pytest.raises(ValueError, match="unknown rule codes in weights: NO_SUCH_RULE"):
        ClientConfig.from_dict(
            {"fiscal_year_end": "2026-12-31", "period_close_date": "2027-01-10", "weights": {"NO_SUCH_RULE": 1}}
        )


def test_requires_fiscal_dates():
    with pytest.raises(ValueError, match="fiscal_year_end"):
        ClientConfig.from_dict({})


def test_to_dict_round_trips():
    config = make_config(approval_threshold=Decimal("250000"))
    assert ClientConfig.from_dict(config.to_dict()) == config


def test_every_rule_has_a_default_weight():
    assert len(DEFAULT_WEIGHTS) == 11


def test_sample_config_loads():
    config = load_config(SAMPLE_DIR / "client_config.json")
    assert config.fiscal_year_end == date(2026, 12, 31)
    assert config.min_account_usage == 2
