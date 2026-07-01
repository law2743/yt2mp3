from __future__ import annotations

import hmac
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.errors import AppError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(max_length=1024)


class LoginAttemptLimiter:
    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, settings: Settings) -> None:
        now = time.monotonic()
        history = self.failures[key]
        cutoff = now - settings.login_failure_window_seconds
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= settings.login_max_failures:
            raise AppError(429, "LOGIN_RATE_LIMITED", "登入失敗次數過多，請稍後再試。", True)

    def fail(self, key: str) -> None:
        self.failures[key].append(time.monotonic())

    def success(self, key: str) -> None:
        self.failures.pop(key, None)


login_attempts = LoginAttemptLimiter()


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.token_secret, salt="yt2mp3-access-token-v1")


def issue_access_token(owner_id: str, settings: Settings) -> str:
    return _serializer(settings).dumps({"sub": owner_id, "v": 1})


def verify_access_token(token: str, settings: Settings) -> str:
    try:
        payload = _serializer(settings).loads(
            token,
            max_age=settings.access_token_ttl_minutes * 60,
        )
        owner_id = str(uuid.UUID(payload["sub"]))
        if payload.get("v") != 1:
            raise ValueError
        return owner_id
    except SignatureExpired as exc:
        raise AppError(401, "TOKEN_EXPIRED", "登入已過期，請重新登入。") from exc
    except (AttributeError, BadSignature, KeyError, TypeError, ValueError) as exc:
        raise AppError(401, "INVALID_TOKEN", "登入憑證無效，請重新登入。") from exc


async def authenticated_owner(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return verify_access_token(token, settings)
    if not settings.authentication_enabled:
        return "00000000-0000-0000-0000-000000000000"
    raise AppError(401, "AUTH_REQUIRED", "請先登入。")


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    client_key = request.client.host if request.client else "unknown"
    login_attempts.check(client_key, settings)
    expected = settings.app_password or ""
    valid = not settings.authentication_enabled or (
        bool(expected) and hmac.compare_digest(payload.password, expected)
    )
    if not valid:
        login_attempts.fail(client_key)
        raise AppError(401, "INVALID_CREDENTIALS", "密碼不正確。")
    login_attempts.success(client_key)
    token = issue_access_token(str(uuid.uuid4()), settings)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
    }


@router.get("/session")
async def session(owner_id: str = Depends(authenticated_owner)):
    return {"authenticated": True, "owner_id": owner_id}
