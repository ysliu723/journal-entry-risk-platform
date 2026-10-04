"""The web application.

    uvicorn app.main:app --reload

Interactive API docs are then at http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db.database import engine
from app.db.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create missing tables at startup. A bigger project would use migrations (e.g. Alembic).
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Journal Entry Risk Platform", version="1.0.0", lifespan=lifespan)
app.include_router(router)
