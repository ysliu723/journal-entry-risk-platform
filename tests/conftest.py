"""Fixtures for tests that need PostgreSQL.

Start the database first:  docker compose up -d db
If it is not running, the tests that need it are skipped, unless
REQUIRE_DATABASE=1 is set (as in CI), where a missing database is an error.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://je_risk:je_risk@localhost:5432/je_risk_test"
)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect():
            pass
    except OperationalError:
        if os.environ.get("REQUIRE_DATABASE") == "1":
            raise  # in CI, skipped database tests would hide a broken setup
        pytest.skip("PostgreSQL is not running (start it with: docker compose up -d db)")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """A session on an empty database. Every table is emptied again after the test."""
    with Session(db_engine, expire_on_commit=False) as session:
        yield session
    table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with db_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
