from contextlib import contextmanager
from threading import Lock

from sqlalchemy import inspect, text

_guard = Lock()
_locks = {}


@contextmanager
def meeting_lock(db, meeting_id):
    key = (db.get_bind().url.database, meeting_id)
    with _guard:
        lock, count = _locks.get(key, (None, 0))
        lock = lock or Lock()
        _locks[key] = (lock, count + 1)
    try:
        with lock:
            yield
    finally:
        with _guard:
            _, count = _locks[key]
            if count == 1:
                del _locks[key]
            else:
                _locks[key] = (lock, count - 1)


def delete_checkpoints(db, meeting_id, *sources):
    connection = db.connection()
    inspector = inspect(connection)
    for table in ("checkpoints", "writes"):
        if inspector.has_table(table):
            for source in sources:
                connection.execute(text(f"DELETE FROM {table} WHERE thread_id = :thread"), {
                    "thread": f"commitments:{meeting_id}:{source}",
                })
