"""Веб-зависимости: текущий пользователь из cookie-сессии.

Сессия живёт в request.scope["session"] (ставит Starlette SessionMiddleware).
Если SessionMiddleware не подключён (session_secret пуст — аккаунты выключены),
обращения безопасно возвращают «не залогинен», сайт работает как раньше.
"""
import asyncio

from fastapi import Request

from lawcheck.db import repo
from lawcheck.db.models import User


def _session(request: Request) -> dict | None:
    return request.scope.get("session")


def session_uid(request: Request) -> int | None:
    sess = _session(request)
    return sess.get("uid") if sess else None


def session_email(request: Request) -> str | None:
    """Email залогиненного — для навигации без похода в БД (шаблоны)."""
    sess = _session(request)
    return sess.get("email") if sess else None


def login_user(request: Request, user: User) -> None:
    sess = _session(request)
    if sess is not None:
        sess["uid"] = user.id
        sess["email"] = user.email


def logout_user(request: Request) -> None:
    sess = _session(request)
    if sess is not None:
        sess.clear()


async def current_user(request: Request) -> User | None:
    uid = session_uid(request)
    if not uid:
        return None
    return await asyncio.to_thread(repo.get_user_by_id, uid)
