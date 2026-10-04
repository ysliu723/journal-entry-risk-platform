"""Database connection settings."""

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://je_risk:je_risk@localhost:5432/je_risk")

# create_engine does not connect yet; connections are opened when first used.
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """One database session per API request, closed when the request ends."""
    with SessionLocal() as session:
        yield session
