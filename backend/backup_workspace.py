"""Take an online SQLite backup, verify it, and remove active session records from the copy."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from app.database import database_path


def backup_workspace(source, destination):
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Select a workspace SQLite file.")
    destination = destination.resolve()
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup)
                if backup.execute("SELECT 1 FROM sqlite_master WHERE name = 'workspace_sessions'").fetchone():
                    backup.execute("DELETE FROM workspace_sessions")
                    backup.commit()
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("The backup failed its integrity check.")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=database_path())
    args = parser.parse_args()
    backup_workspace(args.source, args.destination)
    print(f"Verified workspace backup: {args.destination.resolve()}")
