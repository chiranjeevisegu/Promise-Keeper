import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def open_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + path.as_posix())

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys = ON")

    return engine


def database_path() -> Path:
    configured_path = os.getenv("PROMISE_KEEPER_DB_PATH")
    if configured_path:
        return Path(configured_path).resolve()
    return Path(__file__).resolve().parents[1] / "data" / "promise_keeper.db"


def upgrade_promise_dates(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        columns = {column["name"] for column in inspect(connection).get_columns("promises")}
        if "due_date" not in columns:
            connection.exec_driver_sql("ALTER TABLE promises ADD COLUMN due_date DATE")
        if "lifecycle_version" not in columns:
            connection.exec_driver_sql("ALTER TABLE promises ADD COLUMN lifecycle_version INTEGER NOT NULL DEFAULT 0")


def session_for(request):
    with Session(request.app.state.engine) as session:
        yield session
