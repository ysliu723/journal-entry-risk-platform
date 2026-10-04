from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
GL = SAMPLE_DIR / "gl_detail.csv"
TB = SAMPLE_DIR / "trial_balance.csv"
CONFIG = SAMPLE_DIR / "client_config.json"


@pytest.fixture
def client(db_session):
    """An HTTP client whose requests use the test database."""

    def test_session():
        yield db_session

    app.dependency_overrides[get_session] = test_session
    # Not used as a context manager, so the app's startup (create tables) does not
    # run against the real database; the db_engine fixture created the test tables.
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, gl=None, tb=None, config=None):
    files = {
        "gl_detail": ("gl_detail.csv", gl if gl is not None else GL.read_bytes(), "text/csv"),
        "trial_balance": ("trial_balance.csv", tb if tb is not None else TB.read_bytes(), "text/csv"),
        "config": ("client_config.json", config if config is not None else CONFIG.read_bytes(), "application/json"),
    }
    return client.post("/runs", files=files)


def entries(client, run_id, **params):
    response = client.get(f"/runs/{run_id}/entries", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_upload_creates_a_run(client):
    response = upload(client)

    assert response.status_code == 201
    run = response.json()
    assert run["entry_count"] == 14
    assert run["integrity_passed"] is True
    assert run["level_counts"] == {"HIGH": 4, "MEDIUM": 1, "LOW": 9}
    assert run["config"]["fiscal_year_end"] == "2026-12-31"
    assert run["load_errors"] == []
    assert run["integrity_findings"] == []


def test_entries_are_ranked_highest_risk_first(client):
    run_id = upload(client).json()["id"]

    page = entries(client, run_id)

    assert page["total"] == 14
    top = page["items"][0]
    assert (top["entry_id"], top["risk_score"], top["risk_level"]) == ("JE000013", 140, "HIGH")
    assert top["amount"] == "99800.00"  # money stays exact: a string, not a float
    assert top["rule_codes"][:2] == ["MISSING_APPROVAL", "NEAR_APPROVAL_THRESHOLD"]


@pytest.mark.parametrize(
    "params, expected_entry_ids",
    [
        ({"risk_level": "HIGH"}, ["JE000013", "JE000007", "JE000010", "JE000014"]),
        ({"rule_code": "SELF_APPROVAL"}, ["JE000009"]),
        ({"account_number": "690900"}, ["JE000010"]),
        ({"prepared_by": "controller01"}, ["JE000013"]),
        ({"entry_id": "JE000014"}, ["JE000014"]),
        ({"risk_level": "MEDIUM", "rule_code": "ROUND_AMOUNT"}, []),
    ],
)
def test_filters(client, params, expected_entry_ids):
    run_id = upload(client).json()["id"]
    page = entries(client, run_id, **params)
    assert [item["entry_id"] for item in page["items"]] == expected_entry_ids
    assert page["total"] == len(expected_entry_ids)


def test_sorting(client):
    run_id = upload(client).json()["id"]
    smallest_first = entries(client, run_id, sort="amount")["items"]
    assert [item["entry_id"] for item in smallest_first[:2]] == ["JE000010", "JE000009"]  # 4,750 then 8,600


def test_pages_cover_every_entry_exactly_once(client):
    run_id = upload(client).json()["id"]

    pages = [entries(client, run_id, page=n, page_size=5) for n in (1, 2, 3)]

    assert [len(p["items"]) for p in pages] == [5, 5, 4]
    seen = [item["id"] for p in pages for item in p["items"]]
    assert len(set(seen)) == 14


def test_entry_detail_explains_the_score(client):
    run_id = upload(client).json()["id"]
    entry_db_id = entries(client, run_id, entry_id="JE000013")["items"][0]["id"]

    detail = client.get(f"/entries/{entry_db_id}").json()

    assert [(line["account_number"], line["debit"], line["credit"]) for line in detail["lines"]] == [
        ("610100", "99800.00", "0.00"),
        ("200100", "0.00", "99800.00"),
    ]
    assert sum(hit["score"] for hit in detail["hits"]) == detail["risk_score"] == 140
    assert detail["hits"][0]["reason"] == "Amount 99,800.00 exceeds 50,000.00 and the entry has no approver."


def test_incomplete_population_is_reported(client):
    rows = GL.read_text(encoding="utf-8").splitlines(keepends=True)
    gl = "".join(row for row in rows if not row.startswith("JE000011,")).encode()

    run = upload(client, gl=gl).json()

    assert run["integrity_passed"] is False
    checks = {finding["check_code"] for finding in run["integrity_findings"]}
    assert checks == {"SEQUENCE_GAP", "TB_ROLLFORWARD"}
    assert client.get(f"/runs/{run['id']}").json()["integrity_findings"] == run["integrity_findings"]


def test_runs_are_listed_newest_first(client):
    first = upload(client).json()["id"]
    second = upload(client).json()["id"]
    assert [run["id"] for run in client.get("/runs").json()] == [second, first]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"config": b'{"fiscal_year_end": "2026-12-31"}'}, "period_close_date"),
        ({"config": b"not json"}, "Expecting value"),
        ({"gl": b"entry_id,line_number\nJE000001,1\n"}, "missing columns"),
        ({"tb": b"account_number\n100100\n"}, "missing columns"),
    ],
)
def test_unusable_uploads_are_rejected(client, kwargs, message):
    response = upload(client, **kwargs)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_invalid_query_parameters_are_rejected(client):
    run_id = upload(client).json()["id"]
    assert client.get(f"/runs/{run_id}/entries", params={"risk_level": "SEVERE"}).status_code == 422
    assert client.get(f"/runs/{run_id}/entries", params={"sort": "description"}).status_code == 422
    assert client.get(f"/runs/{run_id}/entries", params={"page_size": 1000}).status_code == 422


def test_not_found(client):
    assert client.get("/runs/999").status_code == 404
    assert client.get("/runs/999/entries").status_code == 404
    assert client.get("/entries/999").status_code == 404
