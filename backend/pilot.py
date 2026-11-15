"""Run a protected single-workspace pilot on loopback or copy its SQLite database."""

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.main import create_app

ROOT = Path(__file__).resolve().parent
PILOT = ROOT / "data" / "pilot"
ITERATIONS = 600_000
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password, salt, ITERATIONS)


@dataclass(frozen=True)
class Credentials:
    username: str
    salt: str
    digest: str

    @classmethod
    def create(cls, username, password):
        if not username.isascii() or not username or ":" in username or not username.isprintable():
            raise ValueError("Choose a printable ASCII username without a colon.")
        if not 12 <= len(password) <= 256:
            raise ValueError("Choose a password between 12 and 256 characters.")
        salt = secrets.token_bytes(16)
        return cls(username, salt.hex(), password_digest(password.encode(), salt).hex())

    @classmethod
    def load(cls, path):
        settings = cls(**json.loads(path.read_text(encoding="utf-8")))
        if len(bytes.fromhex(settings.salt)) != 16 or len(bytes.fromhex(settings.digest)) != 32:
            raise ValueError("Invalid pilot password hash record.")
        return settings

    def matches(self, authorization):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "basic" or not token or len(token) > 2048:
            return False
        try:
            username, password = base64.b64decode(token, validate=True).split(b":", 1)
        except (ValueError, binascii.Error):
            return False
        if not hmac.compare_digest(self.username.encode(), username):
            return False
        expected = bytes.fromhex(self.digest)
        actual = password_digest(password, bytes.fromhex(self.salt))
        return hmac.compare_digest(expected, actual)


def create_pilot_app(database, credentials, port, frontend=None):
    app = create_app(database)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if frontend is not None:
        app.mount("/", StaticFiles(directory=frontend, html=True))

    @app.middleware("http")
    async def protect_workspace(request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in hosts:
            response = JSONResponse({"detail": "Use the local pilot address."}, status_code=400)
        elif not await run_in_threadpool(credentials.matches, request.headers.get("authorization", "")):
            response = JSONResponse({"detail": "Sign in to the pilot workspace."}, status_code=401, headers={
                "WWW-Authenticate": 'Basic realm="Promise Keeper Pilot", charset="UTF-8"',
            })
        elif request.method not in SAFE_METHODS and request.headers.get("origin") != f"http://{host}":
            response = JSONResponse({"detail": "This action requires the pilot workspace origin."}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    return app


def snapshot_database(source, destination):
    source, destination = source.resolve(strict=True), destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a new destination without overwriting a workspace or a previous backup.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live:
            with closing(sqlite3.connect(destination)) as snapshot:
                live.backup(snapshot)
                if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite backup failed its integrity check.")
    except (OSError, sqlite3.Error, ValueError):
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a local password hash record.")
    init.add_argument("--username", default="pilot")
    init.add_argument("--credentials", type=Path, default=PILOT / "credentials.json")
    serve = commands.add_parser("serve", help="Open the protected workspace on loopback.")
    serve.add_argument("--credentials", type=Path, default=PILOT / "credentials.json")
    serve.add_argument("--database", type=Path, default=PILOT / "workspace.sqlite3")
    serve.add_argument("--port", type=int, default=8765)
    backup = commands.add_parser("backup", help="Take an online SQLite snapshot into a new file.")
    backup.add_argument("--source", type=Path, default=PILOT / "workspace.sqlite3")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            password = getpass.getpass("Pilot password: ")
            if password != getpass.getpass("Repeat password: "):
                raise ValueError("Passwords did not match.")
            settings = Credentials.create(args.username, password)
            args.credentials.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(args.credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(asdict(settings), output)
            print("Saved the pilot password hash.")
        elif args.command == "backup":
            snapshot_database(args.source, args.destination)
            print("SQLite snapshot passed its integrity check.")
        else:
            import uvicorn

            if not 1024 <= args.port <= 65535:
                raise ValueError("Choose a port between 1024 and 65535.")
            frontend = ROOT.parent / "frontend" / "dist"
            if not (frontend / "index.html").is_file():
                raise ValueError("Run npm.cmd run build from frontend before starting the pilot.")
            app = create_pilot_app(args.database.resolve(), Credentials.load(args.credentials), args.port, frontend)
            uvicorn.run(app, host="127.0.0.1", port=args.port)
    except (OSError, TypeError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Pilot setup: {error}\n")


if __name__ == "__main__":
    main()
