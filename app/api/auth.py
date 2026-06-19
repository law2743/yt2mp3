from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings

router = APIRouter()


def is_authenticated(request: Request, settings: Settings) -> bool:
    return not settings.authentication_enabled or bool(request.session.get("authenticated"))


@router.post("/login")
async def login(
    request: Request,
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    expected = settings.app_password or ""
    if expected and hmac.compare_digest(password, expected):
        request.session.clear()
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

