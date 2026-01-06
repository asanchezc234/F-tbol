from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Session:
    with Session(engine) as session:
        yield session

