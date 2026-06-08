"""Клиент сервиса ЕГРЮЛ/ЕГРИП ФНС (egrul.nalog.ru).

Двухэтапный запрос:
  1. POST / с form-полем `query=<ИНН>` → возвращает {"t": "<token>", "captchaRequired": bool}
  2. GET /search-result/<token> → возвращает {"rows": [...]} с найденными записями

Контракт: при любой неполадке (сеть, капча, изменение формата) возвращаем None
и пишем причину в last_error — потребитель решает, как это интерпретировать.
"""
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)

_BASE_URL = "https://egrul.nalog.ru"
_UA = "Mozilla/5.0 (LawCheck/0.1; +https://lawchek.ru/bot)"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass
class EgrulRecord:
    inn: str
    ogrn: str
    short_name: str
    full_name: str
    kind: str  # "ul" — юр.лицо, "fl" — ИП
    region: str = ""
    registered_at: str = ""  # дата гос. регистрации (дд.мм.гггг)
    director: str = ""


@dataclass
class EgrulLookupResult:
    record: EgrulRecord | None
    error: str = ""  # пустая строка = успех; иначе причина (timeout/captcha/notfound/etc.)


def _do_lookup(inn: str) -> EgrulLookupResult:
    try:
        with httpx.Client(headers={"User-Agent": _UA}, timeout=_TIMEOUT) as client:
            r = client.post(_BASE_URL + "/", data={"query": inn, "PreventChromeAutocomplete": ""})
            r.raise_for_status()
            data = r.json()
            if data.get("captchaRequired"):
                return EgrulLookupResult(record=None, error="captcha_required")
            token = data.get("t")
            if not token:
                return EgrulLookupResult(record=None, error="no_token")

            # ФНС иногда нужно дать пару секунд на обработку
            time.sleep(1.0)
            r2 = client.get(f"{_BASE_URL}/search-result/{token}")
            r2.raise_for_status()
            payload = r2.json()
            rows = payload.get("rows") or []
            if not rows:
                return EgrulLookupResult(record=None, error="not_found")

            row = rows[0]
            return EgrulLookupResult(record=EgrulRecord(
                inn=row.get("i", ""),
                ogrn=row.get("o", ""),
                short_name=row.get("c", ""),
                full_name=row.get("n", ""),
                kind=row.get("k", ""),
                region=row.get("rn", ""),
                registered_at=row.get("r", ""),
                director=row.get("g", ""),
            ))
    except httpx.TimeoutException:
        return EgrulLookupResult(record=None, error="timeout")
    except httpx.HTTPError as e:
        log.warning("egrul http error: %s", e)
        return EgrulLookupResult(record=None, error=f"http_{type(e).__name__}")
    except Exception as e:
        log.exception("egrul unexpected error")
        return EgrulLookupResult(record=None, error=f"unexpected_{type(e).__name__}")


@lru_cache(maxsize=512)
def lookup_by_inn(inn: str) -> EgrulLookupResult:
    return _do_lookup(inn)
