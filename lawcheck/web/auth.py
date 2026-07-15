"""Аккаунты: регистрация, вход, выход, подтверждение email, сброс пароля.

Под-роутер по образцу web/blog.py — общий экземпляр `templates` проставляется
из web/routes.py. Письма шлём через notify/mailer (на деве — console-бэкенд).
"""
import asyncio
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from lawcheck.config import settings
from lawcheck.db import repo
from lawcheck.notify import mailer
from lawcheck.web import deps, security

log = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates = None  # проставляется в web/routes.py

_VERIFY_TTL_H = 48
_RESET_TTL_H = 1


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1]


def _base() -> str:
    return settings.site_base_url.rstrip("/")


def _send_verification(user) -> None:
    """Создаёт токен и шлёт письмо подтверждения email (sync — под to_thread)."""
    token = repo.create_auth_token(user.id, "verify_email", _VERIFY_TTL_H)
    link = f"{_base()}/verify-email?token={token}"
    html = (
        f"<p>Здравствуйте!</p><p>Подтвердите email для аккаунта LawCheck — "
        f"перейдите по ссылке (действует {_VERIFY_TTL_H} часа):</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>Если вы не регистрировались — просто проигнорируйте письмо.</p>"
    )
    mailer.send_email(user.email, "Подтверждение email · LawCheck", html)


def _send_reset(user) -> None:
    """Создаёт токен и шлёт письмо сброса пароля (sync — под to_thread)."""
    token = repo.create_auth_token(user.id, "reset_password", _RESET_TTL_H)
    link = f"{_base()}/reset-password?token={token}"
    html = (
        f"<p>Здравствуйте!</p><p>Вы запросили сброс пароля в LawCheck. "
        f"Задайте новый пароль по ссылке (действует {_RESET_TTL_H} час):</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>Если вы не запрашивали сброс — просто проигнорируйте письмо, "
        f"пароль останется прежним.</p>"
    )
    mailer.send_email(user.email, "Сброс пароля · LawCheck", html)


def _message(request: Request, title: str, message: str,
             cta_href: str = "/", cta_label: str = "На главную", status: int = 200):
    return templates.TemplateResponse(request, "message.html", {
        "title": title, "message": message, "cta_href": cta_href, "cta_label": cta_label,
    }, status_code=status)


# === Регистрация / вход / выход ===

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
    await asyncio.to_thread(_send_verification, user)
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
    if user.email_verified_at is not None:
        # Подтверждённый email — подцепим прошлые заказы/сканы к аккаунту.
        await asyncio.to_thread(repo.claim_for_user, user.id, user.email)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await deps.current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    scans = await asyncio.to_thread(repo.list_scans_for_user, user.id)
    orders = await asyncio.to_thread(repo.list_orders_for_user, user.id)
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user, "scans": scans, "orders": orders,
        "verified": user.email_verified_at is not None,
    })


@router.post("/logout")
async def logout(request: Request):
    deps.logout_user(request)
    return RedirectResponse(url="/", status_code=303)


# === Подтверждение email ===

@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: str = ""):
    uid = await asyncio.to_thread(repo.consume_auth_token, token, "verify_email")
    if uid is None:
        return _message(request, "Ссылка недействительна",
                        "Ссылка подтверждения просрочена или уже использована. "
                        "Войдите и запросите письмо заново.",
                        cta_href="/login", cta_label="Войти", status=400)
    await asyncio.to_thread(repo.set_email_verified, uid)
    if deps.session_uid(request) == uid:
        deps.mark_session_verified(request)
    # Email подтверждён — безопасно подцепить прошлые заказы/сканы этого email.
    user = await asyncio.to_thread(repo.get_user_by_id, uid)
    if user:
        await asyncio.to_thread(repo.claim_for_user, user.id, user.email)
    return _message(request, "Email подтверждён ✅",
                    "Спасибо! Ваш email подтверждён — аккаунт активен.",
                    cta_href="/", cta_label="На главную")


@router.post("/resend-verification")
async def resend_verification(request: Request):
    uid = deps.session_uid(request)
    if uid:
        user = await asyncio.to_thread(repo.get_user_by_id, uid)
        if user and user.email_verified_at is None:
            await asyncio.to_thread(_send_verification, user)
    return RedirectResponse(url="/?vsent=1", status_code=303)


# === Сброс пароля ===

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_form(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    # Не раскрываем, есть ли аккаунт (защита от перебора email) — ответ всегда один.
    if _valid_email(email):
        user = await asyncio.to_thread(repo.get_user_by_email, email)
        if user is not None:
            await asyncio.to_thread(_send_reset, user)
    return _message(request, "Проверьте почту",
                    "Если аккаунт с таким email существует, мы отправили письмо "
                    "со ссылкой для сброса пароля. Ссылка действует 1 час.",
                    cta_href="/login", cta_label="Ко входу")


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_form(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "reset_password.html", {"token": token})


@router.post("/reset-password", response_class=HTMLResponse)
async def reset(request: Request, token: str = Form(...), password: str = Form(...)):
    if len(password) < 8:
        return templates.TemplateResponse(request, "reset_password.html",
                                          {"token": token, "error": "Пароль — минимум 8 символов."},
                                          status_code=422)
    uid = await asyncio.to_thread(repo.consume_auth_token, token, "reset_password")
    if uid is None:
        return _message(request, "Ссылка недействительна",
                        "Ссылка сброса просрочена или уже использована. "
                        "Запросите новую на странице входа.",
                        cta_href="/forgot-password", cta_label="Запросить заново", status=400)
    await asyncio.to_thread(repo.set_user_password, uid, security.hash_password(password))
    # После сброса — на вход, пусть авторизуется новым паролём.
    deps.logout_user(request)
    log.info("account: пароль сброшен для user #%s", uid)
    return _message(request, "Пароль обновлён ✅",
                    "Готово. Войдите с новым паролём.",
                    cta_href="/login", cta_label="Войти")
