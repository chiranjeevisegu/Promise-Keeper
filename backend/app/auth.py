import hashlib
import hmac
import os
import secrets
from collections import deque
from dataclasses import dataclass
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import WorkspaceSession

COOKIE = "pk_workspace"
SESSION_SECONDS = 12 * 60 * 60
SAFE = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/auth/login")}
router = APIRouter(prefix="/api/auth")


@dataclass(frozen=True, repr=False)
class WorkspaceAccess:
    password: str | None
    origin: str

    @classmethod
    def configured(cls):
        required = os.getenv("PROMISE_KEEPER_AUTH_REQUIRED") == "1"
        password = os.getenv("PROMISE_KEEPER_PASSWORD") or None
        if (required or password is not None) and (password is None or len(password.strip()) < 12):
            raise RuntimeError("Configure a private workspace password of at least 12 characters.")
        origin = os.getenv("PROMISE_KEEPER_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(origin)
        if password and (
            parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path
            or parsed.query or parsed.fragment or parsed.username or parsed.password
        ):
            raise RuntimeError("PROMISE_KEEPER_ORIGIN must be the workspace origin, including scheme and port if used.")
        if password and parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Use HTTPS for private workspace access outside loopback.")
        return cls(password, origin)

    def digest(self, token):
        return hmac.new(self.password.encode(), token.encode(), hashlib.sha256).hexdigest()

    def permits(self, request):
        if self.password is None:
            return True
        token = request.cookies.get(COOKIE, "")
        if len(token) != 43 or not token.isascii():
            return False
        with Session(request.app.state.engine) as db:
            session = db.get(WorkspaceSession, self.digest(token))
            return bool(session and session.expires_at > time())


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class LoginThrottle:
    def __init__(self):
        self.lock = Lock()
        self.attempts = deque()

    def check(self):
        with self.lock:
            now = monotonic()
            while self.attempts and now - self.attempts[0] >= 60:
                self.attempts.popleft()
            if len(self.attempts) >= 12:
                raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.",
                                    headers={"Retry-After": "60"})
            self.attempts.append(now)


@router.get("/status")
def status(request: Request):
    access = request.app.state.access
    return {"required": access.password is not None, "authenticated": access.permits(request)}


@router.post("/login")
def login(body: Login, request: Request):
    access = request.app.state.access
    if access.password is None:
        return {"authenticated": True}
    request.app.state.login_throttle.check()
    if not hmac.compare_digest(body.password.encode(), access.password.encode()):
        raise HTTPException(401, "Incorrect workspace password.")
    token = secrets.token_urlsafe(32)
    now = int(time())
    with Session(request.app.state.engine) as db:
        db.execute(delete(WorkspaceSession).where(WorkspaceSession.expires_at <= now))
        db.add(WorkspaceSession(digest=access.digest(token), expires_at=now + SESSION_SECONDS))
        db.commit()
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=access.origin.startswith("https://"),
        samesite="strict", path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    access = request.app.state.access
    token = request.cookies.get(COOKIE)
    if access.password is not None and token:
        with Session(request.app.state.engine) as db:
            db.execute(delete(WorkspaceSession).where(WorkspaceSession.digest == access.digest(token)))
            db.commit()
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=access.origin.startswith("https://"), samesite="strict")
    return response


async def protect_api(request, call_next):
    path = request.url.path
    access = request.app.state.access
    if (path == "/api" or path.startswith("/api/")) and access.password is not None:
        if request.method not in SAFE and request.headers.get("origin") != access.origin:
            response = JSONResponse({"detail": "Open the workspace at its configured address."}, status_code=403)
        elif (request.method, path) not in PUBLIC and not await run_in_threadpool(access.permits, request):
            response = JSONResponse({"detail": "Sign in to access this workspace."}, status_code=401)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    return await call_next(request)
