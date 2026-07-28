"""Клиент интернет-эквайринга Точки: платёжные ссылки.

Документация: https://developers.tochka.com/docs/tochka-api/internet-acquiring-integration

Поток:
1. create_payment() → POST /acquiring/v1.0/payments → банк возвращает
   operationId и paymentLink; клиента редиректим на ссылку.
2. Банк шлёт вебхук acquiringInternetPayment (JWT). Вебхук используем только
   как триггер: фактический статус ВСЕГДА перепроверяем авторизованным
   get_operation_status() — это снимает вопрос подделки вебхука.

ВНИМАНИЕ: имена полей выверены по публичной документации; финальная сверка —
на тестовом платеже в 1 ₽ после подключения эквайринга в ЛК Точки.
"""
import logging
from dataclasses import dataclass

import httpx

from lawcheck.config import settings

log = logging.getLogger(__name__)

_PAID_STATUSES = {"APPROVED", "AUTHORIZED"}  # APPROVED — списание прошло


class TochkaNotConfigured(Exception):
    """Эквайринг ещё не настроен (нет JWT) — используйте fallback-режим."""


class TochkaBadResponse(ValueError):
    """Банк ответил 200, но не тем, чего мы ждём.

    Наследует ValueError, чтобы один `except` покрывал и это, и разбор не-JSON.
    """


@dataclass
class PaymentLink:
    operation_id: str
    url: str


def is_configured() -> bool:
    return bool(settings.tochka_jwt and settings.tochka_customer_code)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.tochka_base_url,
        headers={"Authorization": f"Bearer {settings.tochka_jwt}"},
        timeout=20,
    )


def create_payment(*, amount_rub: int, purpose: str, order_id: str) -> PaymentLink:
    """Создаёт платёжную ссылку (карты + СБП). Суммы — в рублях."""
    if not is_configured():
        raise TochkaNotConfigured
    body = {
        "Data": {
            "customerCode": settings.tochka_customer_code,
            "amount": f"{amount_rub}.00",
            "purpose": purpose,
            "paymentMode": ["card", "sbp"],
            "redirectUrl": f"{settings.site_base_url}/pay/success?order={order_id}",
            "failRedirectUrl": f"{settings.site_base_url}/pay/fail?order={order_id}",
        }
    }
    if settings.tochka_merchant_id:
        body["Data"]["merchantId"] = settings.tochka_merchant_id
    with _client() as c:
        r = c.post("/acquiring/v1.0/payments", json=body)
        r.raise_for_status()
        payload = r.json()
    data = payload.get("Data") if isinstance(payload, dict) else None
    operation_id = (data or {}).get("operationId") or ""
    url = (data or {}).get("paymentLink") or ""
    # Пустые поля молча пропускать нельзя: клиент уедет на пустую ссылку, а заказ
    # останется без operation_id — и подтвердить оплату будет уже нечем никогда
    # (pay_success требует o.operation_id). Лучше показать fallback-страницу.
    if not operation_id or not url:
        log.error("tochka: ответ без operationId/paymentLink: %.500s", r.text)
        raise TochkaBadResponse("банк не вернул платёжную ссылку")
    return PaymentLink(operation_id=operation_id, url=url)


def get_operation_status(operation_id: str) -> str:
    """Статус операции из API банка (источник истины, не вебхук)."""
    if not is_configured():
        raise TochkaNotConfigured
    with _client() as c:
        r = c.get(f"/acquiring/v1.0/payments/{operation_id}")
        r.raise_for_status()
        payload = r.json()
    data = payload.get("Data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise TochkaBadResponse("в ответе банка нет объекта Data")
    ops = data.get("Operation")
    first = ops[0] if isinstance(ops, list) and ops else data
    status = first.get("status") if isinstance(first, dict) else None
    return str(status or "").upper()


def is_paid(operation_id: str) -> bool:
    """Оплачена ли операция. Никогда не бросает: вызывается на пути клиента,
    только что заплатившего, и в вебхуке банка.

    ValueError покрывает и TochkaBadResponse, и JSONDecodeError на не-JSON
    ответе. Раньше ловился только httpx.HTTPError, поэтому неожиданный формат
    ответа давал 500 клиенту и бесконечные ретраи вебхука со стороны банка.
    """
    try:
        return get_operation_status(operation_id) in _PAID_STATUSES
    except (httpx.HTTPError, ValueError) as e:
        log.warning("tochka: не удалось проверить операцию %s: %s", operation_id, e)
        return False
