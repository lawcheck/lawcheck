"""Клиент реестра операторов персональных данных Роскомнадзора (pd.rkn.gov.ru).

ВАЖНО: реестр часто блокирует/таймаутит запросы из-за пределов РФ. На VPS в РФ
работает стабильно. Клиент консервативен: при любой неполадке возвращает
LookupResult(error=...) — потребитель эмитит INFO «не удалось проверить»,
а не CRITICAL «не зарегистрирован».

Поиск через GET /operators-registry/operators-list/?OrgInn=<ИНН>
Возвращается HTML; парсим:
  - текст «Записи не найдены» → not_found
  - ссылка вида /operators-registry/operator/?id=NNN → найден; вытаскиваем
    регистрационный номер из ?id и наименование оператора из текста ссылки.
"""
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

import httpx

log = logging.getLogger(__name__)

_BASE_URL = "https://pd.rkn.gov.ru"
_SEARCH_PATH = "/operators-registry/operators-list/"
_UA = "Mozilla/5.0 (LawCheck/0.1; +https://lawchek.ru/bot)"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# Маркер найденной записи в HTML: ряд таблицы results имеет класс clmn1.
_RESULT_ROW_MARKER = re.compile(r"class\s*=\s*['\"]clmn1['\"]", re.I)
# Внутри найденной строки: регистрационный номер в <nobr> и
# название оператора в <a href="?id=...">
_OPERATOR_BLOCK_RE = re.compile(
    r"class\s*=\s*['\"]clmn1['\"].*?"
    r"<nobr>\s*(?P<id>[\d\-]+)\s*</nobr>.*?"
    r"<a\s+href\s*=\s*['\"]\?id=[\d\-]+['\"][^>]*>(?P<name>[^<]+)</a>",
    re.I | re.S,
)


@dataclass
class RknOperator:
    inn: str
    registry_id: str         # внутренний id РКН
    name: str                # наименование, как в реестре
    detail_url: str = ""


@dataclass
class RknLookupResult:
    operator: RknOperator | None
    not_found: bool = False  # True если реестр явно сказал «не найдено»
    error: str = ""          # пустая строка = успех (включая not_found)


def _parse_html(inn: str, html: str) -> RknLookupResult:
    # Если в HTML вообще нет строки-результата (class='clmn1') — записей нет.
    if not _RESULT_ROW_MARKER.search(html):
        return RknLookupResult(operator=None, not_found=True)
    m = _OPERATOR_BLOCK_RE.search(html)
    if m:
        rid = m.group("id").strip()
        return RknLookupResult(operator=RknOperator(
            inn=inn,
            registry_id=rid,
            name=re.sub(r"\s+", " ", m.group("name")).strip(),
            detail_url=f"{_BASE_URL}/operators-registry/operators-list/?id={rid}",
        ))
    return RknLookupResult(operator=None, error="unparseable_response")


def _do_lookup(inn: str) -> RknLookupResult:
    try:
        with httpx.Client(headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True) as client:
            r = client.get(_BASE_URL + _SEARCH_PATH, params={"act": "search", "inn": inn})
            r.raise_for_status()
            return _parse_html(inn, r.text)
    except httpx.TimeoutException:
        return RknLookupResult(operator=None, error="timeout")
    except httpx.HTTPError as e:
        log.warning("rkn http error: %s", e)
        return RknLookupResult(operator=None, error=f"http_{type(e).__name__}")
    except Exception as e:
        log.exception("rkn unexpected error")
        return RknLookupResult(operator=None, error=f"unexpected_{type(e).__name__}")


@lru_cache(maxsize=512)
def lookup_by_inn(inn: str) -> RknLookupResult:
    return _do_lookup(inn)
