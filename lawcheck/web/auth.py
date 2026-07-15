"""Аккаунты: регистрация, вход, выход.

Под-роутер по образцу web/blog.py — общий экземпляр `templates` проставляется
из web/routes.py. Подключается только когда включены сессии (session_secret).
"""
import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lawcheck.db import repo
from lawcheck.web import deps, security

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # проставляется в web/routes.py


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_class=HTMLResponse)
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    err = None
    if not _valid_email(email):
        err = "Проверьте адрес email."
    elif len(password) < 8:
        err = "Пароль — минимум 8 символов."
    if err:
        return templates.TemplateResponse(request, "register.html",
                                          {"error": err, "email": email}, status_code=422)
    user = await asyncio.to_thread(repo.create_user, email, security.hash_password(password))
    if user is None:
        return templates.TemplateResponse(request, "register.html",
                                          {"error": "На этот email уже есть аккаунт — войдите.",
                                           "email": email}, status_code=409)
    deps.login_user(request, user)
    log.info("account: зарегистрирован %s (#%s)", email, user.id)
    return RedirectResponse(url="/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    user = await asyncio.to_thread(repo.get_user_by_email, email)
    if user is None or not security.verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "Неверный email или пароль.", "email": email},
                                          status_code=401)
    deps.login_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    deps.logout_user(request)
    return RedirectResponse(url="/", status_code=303)
