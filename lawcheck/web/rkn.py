"""Продукт «Уведомление в РКН»: посадочная /uvedomlenie-rkn и бесплатная
проверка по реестру операторов /reestr-rkn (лид-магнит).

Подключается без гейта SEO_ENABLED: посадочная — цель рекламной кампании
Директа и должна жить независимо от флага SEO-контента.
"""
import asyncio
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from lawcheck.external.rkn_operators import lookup_by_inn
from lawcheck.utils.inn_ogrn import is_valid_inn

router = APIRouter()
templates = None  # задаётся из routes.py при подключении


@router.get("/uvedomlenie-rkn", response_class=HTMLResponse)
async def landing_rkn(request: Request):
    return templates.TemplateResponse(request, "landing_rkn.html", {})


@router.get("/reestr-rkn", response_class=HTMLResponse)
async def rkn_check_page(request: Request):
    return templates.TemplateResponse(
        request, "rkn_check.html", {"state": None, "inn": "", "op": None})


@router.post("/reestr-rkn", response_class=HTMLResponse)
async def rkn_check(request: Request, inn: str = Form("")):
    """Проверка ИНН по реестру операторов pd.rkn.gov.ru.

    Состояния: invalid (не ИНН) / found / not_found / error (реестр недоступен —
    он часто отвечает только из РФ; на проде это редкий случай).
    """
    inn_digits = re.sub(r"\D", "", inn)
    if not is_valid_inn(inn_digits):
        return templates.TemplateResponse(
            request, "rkn_check.html",
            {"state": "invalid", "inn": inn.strip(), "op": None})
    result = await asyncio.to_thread(lookup_by_inn, inn_digits)
    if result.error:
        state = "error"
    elif result.operator is not None:
        state = "found"
    else:
        state = "not_found"
    return templates.TemplateResponse(
        request, "rkn_check.html",
        {"state": state, "inn": inn_digits, "op": result.operator})
